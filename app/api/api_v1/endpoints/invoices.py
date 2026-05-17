from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.supabase import get_supabase_client
from app.schemas.invoice import Invoice, InvoiceCreate, InvoiceUpdate
from app.schemas.user import User

router = APIRouter()


def _normalize_invoice(row: dict) -> dict:
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "case_id": row.get("case_id"),
        "due_date": row["due_date"],
        "description": row["description"],
        "price": row["price"],
        "status": row["status"],
        "client": row.get("clients"),
        "case": row.get("cases"),
    }


@router.get("/", response_model=List[Invoice])
async def get_invoices(
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("invoices").select("*, clients(id, name), cases(id, name)").execute()
    return [_normalize_invoice(row) for row in response.data or []]


@router.post("/", response_model=Invoice, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    *,
    invoice_in: InvoiceCreate,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("invoices").insert(invoice_in.model_dump(mode="json")).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create invoice")
    created = supabase.table("invoices").select("*, clients(id, name), cases(id, name)").eq("id", response.data[0]["id"]).single().execute()
    return _normalize_invoice(created.data)


@router.put("/{invoice_id}", response_model=Invoice)
async def update_invoice(
    *,
    invoice_id: str,
    invoice_in: InvoiceUpdate,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("invoices").update(invoice_in.model_dump(mode="json", exclude_unset=True)).eq("id", invoice_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    updated = supabase.table("invoices").select("*, clients(id, name), cases(id, name)").eq("id", invoice_id).single().execute()
    return _normalize_invoice(updated.data)


@router.delete("/{invoice_id}")
async def delete_invoice(
    *,
    invoice_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("invoices").delete().eq("id", invoice_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"success": True}
