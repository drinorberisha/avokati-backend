from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError

from app.core.auth import get_current_user
from app.core.tenancy import require_office, assert_in_office, get_user_supabase_client
from app.schemas.case import Case, CaseCreate, CaseStatus, CaseUpdate
from app.schemas.case_milestone import CaseMilestone, CaseMilestoneCreate, CaseMilestoneUpdate
from app.schemas.user import User

router = APIRouter()


def _normalize_case(row: dict) -> dict:
    return {
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


def _normalize_milestone(row: dict) -> dict:
    return {
        "id": row["id"],
        "case_id": row["case_id"],
        "title": row["title"],
        "description": row.get("description"),
        "due_date": row.get("due_date"),
        "status": row["status"],
        "priority": row["priority"],
    }


@router.post("/", response_model=Case, status_code=status.HTTP_201_CREATED)
async def create_case(
    *,
    case_in: CaseCreate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    payload = case_in.model_dump(mode="json")
    # The referenced client must belong to the caller's office.
    assert_in_office(supabase, "clients", payload.get("client_id"), office_id, detail="Client not found")
    payload["office_id"] = office_id
    response = supabase.table("cases").insert(payload).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create case")
    created = (
        supabase.table("cases")
        .select("*, clients(id, name, email, phone)")
        .eq("id", response.data[0]["id"])
        .eq("office_id", office_id)
        .single()
        .execute()
    )
    return _normalize_case(created.data)


@router.get("/", response_model=List[Case])
async def get_cases(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
    client_id: Optional[UUID] = Query(None),
    status: Optional[CaseStatus] = Query(None),
) -> Any:
    query = supabase.table("cases").select("*, clients(id, name, email, phone)").eq("office_id", office_id)
    if client_id:
        query = query.eq("client_id", str(client_id))
    if status:
        query = query.eq("status", status.value)
    response = query.execute()
    return [_normalize_case(row) for row in response.data or []]


@router.get("/{case_id}", response_model=Case)
async def read_case(
    *,
    case_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    response = (
        supabase.table("cases")
        .select("*, clients(id, name, email, phone)")
        .eq("id", case_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Case not found")
    return _normalize_case(response.data[0])


@router.put("/{case_id}", response_model=Case)
async def update_case(
    *,
    case_id: str,
    case_in: CaseUpdate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    try:
        update_data = case_in.model_dump(mode="json", exclude_unset=True)
        update_data.pop("office_id", None)
        # If reassigning the client, the new client must also be in this office.
        if update_data.get("client_id"):
            assert_in_office(supabase, "clients", update_data["client_id"], office_id, detail="Client not found")
        response = (
            supabase.table("cases")
            .update(update_data)
            .eq("id", case_id)
            .eq("office_id", office_id)
            .execute()
        )
        if not response.data:
            raise HTTPException(status_code=404, detail="Case not found")
        updated = (
            supabase.table("cases")
            .select("*, clients(id, name, email, phone)")
            .eq("id", case_id)
            .eq("office_id", office_id)
            .single()
            .execute()
        )
        return _normalize_case(updated.data)
    except HTTPException:
        raise
    except APIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{case_id}")
async def delete_case(
    *,
    case_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    try:
        # Ownership guard before cascading.
        assert_in_office(supabase, "cases", case_id, office_id, detail="Case not found")
        supabase.table("documents").delete().eq("case_id", case_id).execute()
        supabase.table("invoices").update({"case_id": None}).eq("case_id", case_id).execute()
        supabase.table("case_milestones").delete().eq("case_id", case_id).execute()
        response = supabase.table("cases").delete().eq("id", case_id).eq("office_id", office_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Case not found")
        return {"success": True}
    except HTTPException:
        raise
    except APIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{case_id}/milestones", response_model=List[CaseMilestone])
async def get_case_milestones(
    *,
    case_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    assert_in_office(supabase, "cases", case_id, office_id, detail="Case not found")
    response = (
        supabase.table("case_milestones")
        .select("id, case_id, title, description, due_date, status, priority")
        .eq("case_id", case_id)
        .eq("office_id", office_id)
        .order("due_date", desc=False)
        .execute()
    )
    return [_normalize_milestone(row) for row in response.data or []]


@router.post("/{case_id}/milestones", response_model=CaseMilestone, status_code=status.HTTP_201_CREATED)
async def create_case_milestone(
    *,
    case_id: str,
    milestone_in: CaseMilestoneCreate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    assert_in_office(supabase, "cases", case_id, office_id, detail="Case not found")
    payload = milestone_in.model_dump(mode="json")
    payload["case_id"] = case_id
    payload["office_id"] = office_id
    response = supabase.table("case_milestones").insert(payload).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create milestone")
    return _normalize_milestone(response.data[0])


@router.put("/{case_id}/milestones/{milestone_id}", response_model=CaseMilestone)
async def update_case_milestone(
    *,
    case_id: str,
    milestone_id: str,
    milestone_in: CaseMilestoneUpdate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    payload = milestone_in.model_dump(mode="json", exclude_unset=True)
    payload.pop("office_id", None)
    response = (
        supabase.table("case_milestones")
        .update(payload)
        .eq("id", milestone_id)
        .eq("case_id", case_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return _normalize_milestone(response.data[0])


@router.delete("/{case_id}/milestones/{milestone_id}")
async def delete_case_milestone(
    *,
    case_id: str,
    milestone_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    response = (
        supabase.table("case_milestones")
        .delete()
        .eq("id", milestone_id)
        .eq("case_id", case_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return {"success": True}
