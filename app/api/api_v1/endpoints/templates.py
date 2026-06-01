from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.supabase import get_supabase_client
from app.core.tenancy import require_office
from app.schemas.template import TemplateCreate, TemplateOut, TemplateUpdate
from app.schemas.user import User

router = APIRouter()

_FIELDS = "id, title, description, category, language, status, content, variables, source_type, created_at, updated_at"


def _normalize(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row.get("description"),
        "category": row.get("category"),
        "language": row.get("language"),
        "status": row.get("status") or "draft",
        "content": row.get("content") or "",
        "variables": row.get("variables") or [],
        "source_type": row.get("source_type") or "manual",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/", response_model=List[TemplateOut])
async def list_templates(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("templates")
        .select(_FIELDS)
        .eq("office_id", office_id)
        .order("updated_at", desc=True)
        .execute()
    )
    return [_normalize(row) for row in resp.data or []]


@router.post("/", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    *,
    template_in: TemplateCreate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = template_in.model_dump(mode="json")
    payload["office_id"] = office_id
    payload["owner_id"] = str(current_user.id)
    payload["source_type"] = "manual"
    resp = supabase.table("templates").insert(payload).execute()
    if not resp.data:
        raise HTTPException(status_code=400, detail="Failed to create template")
    return _normalize(resp.data[0])


@router.get("/{template_id}", response_model=TemplateOut)
async def read_template(
    *,
    template_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("templates")
        .select(_FIELDS)
        .eq("id", template_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return _normalize(resp.data[0])


@router.patch("/{template_id}", response_model=TemplateOut)
async def update_template(
    *,
    template_id: str,
    template_in: TemplateUpdate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    update_data = template_in.model_dump(mode="json", exclude_unset=True)
    # Never let a payload move a template to another office.
    update_data.pop("office_id", None)
    if not update_data:
        raise HTTPException(status_code=400, detail="Nothing to update")
    resp = (
        supabase.table("templates")
        .update(update_data)
        .eq("id", template_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return _normalize(resp.data[0])


@router.delete("/{template_id}")
async def delete_template(
    *,
    template_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("templates")
        .delete()
        .eq("id", template_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True}
