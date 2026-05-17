from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.supabase import get_supabase_client
from app.schemas.client import Client, ClientCreate, ClientUpdate
from app.schemas.user import User

router = APIRouter()


def _normalize_client(row: dict, case_ids: list[str] | None = None) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row.get("phone"),
        "address": row.get("address"),
        "cases": case_ids or [],
        "client_since": row.get("client_since") or row.get("created_at"),
    }


def _case_ids_by_client(supabase, client_ids: list[str]) -> dict[str, list[str]]:
    if not client_ids:
        return {}
    response = supabase.table("cases").select("id, client_id").in_("client_id", client_ids).execute()
    grouped: dict[str, list[str]] = {client_id: [] for client_id in client_ids}
    for case in response.data or []:
        grouped.setdefault(case["client_id"], []).append(case["id"])
    return grouped


@router.post("/", response_model=Client, status_code=status.HTTP_201_CREATED)
async def create_client(
    *,
    client_in: ClientCreate,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    existing = supabase.table("clients").select("id").eq("email", client_in.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="A client with this email already exists")

    response = supabase.table("clients").insert(client_in.model_dump(mode="json")).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create client")
    return _normalize_client(response.data[0], [])


@router.get("/", response_model=List[Client])
async def get_clients(
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("clients").select("id, name, email, phone, address, created_at, client_since").execute()
    clients = response.data or []
    grouped = _case_ids_by_client(supabase, [client["id"] for client in clients])
    return [_normalize_client(client, grouped.get(client["id"], [])) for client in clients]


@router.get("/{client_id}", response_model=Client)
async def read_client(
    *,
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("clients").select("id, name, email, phone, address, created_at, client_since").eq("id", client_id).single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Client not found")
    grouped = _case_ids_by_client(supabase, [client_id])
    return _normalize_client(response.data, grouped.get(client_id, []))


@router.put("/{client_id}", response_model=Client)
async def update_client(
    *,
    client_id: str,
    client_in: ClientUpdate,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    update_data = client_in.model_dump(mode="json", exclude_unset=True)
    response = supabase.table("clients").update(update_data).eq("id", client_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Client not found")
    grouped = _case_ids_by_client(supabase, [client_id])
    return _normalize_client(response.data[0], grouped.get(client_id, []))


@router.delete("/{client_id}")
async def delete_client(
    *,
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    cases_response = supabase.table("cases").select("id").eq("client_id", client_id).execute()
    case_ids = [case["id"] for case in cases_response.data or []]

    supabase.table("documents").delete().eq("client_id", client_id).execute()
    supabase.table("invoices").delete().eq("client_id", client_id).execute()
    if case_ids:
        supabase.table("documents").delete().in_("case_id", case_ids).execute()
        supabase.table("case_milestones").delete().in_("case_id", case_ids).execute()
        supabase.table("invoices").delete().in_("case_id", case_ids).execute()
        supabase.table("cases").delete().in_("id", case_ids).execute()

    response = supabase.table("clients").delete().eq("id", client_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True}


@router.get("/{client_id}/cases")
async def get_client_cases(
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("cases").select("*, clients(id, name, email, phone)").eq("client_id", client_id).execute()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "client_id": row["client_id"],
            "status": row["status"],
            "court": row.get("court"),
            "judge": row.get("judge"),
            "description": row.get("description"),
            "client": row.get("clients"),
        }
        for row in response.data or []
    ]


@router.get("/{client_id}/metrics")
async def get_client_metrics(
    client_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("cases").select("id, status").eq("client_id", client_id).execute()
    cases = response.data or []
    return {
        "total_cases": len(cases),
        "active_cases": len([case for case in cases if case["status"] == "open"]),
        "completed_cases": len([case for case in cases if case["status"] == "closed"]),
    }
