from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.supabase import get_supabase_client
from app.core.tenancy import require_office
from app.schemas.event import Event, EventCreate, EventUpdate
from app.schemas.user import User

router = APIRouter()


@router.get("/", response_model=List[Event])
async def get_events(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = (
        supabase.table("events")
        .select("id, title, type, description, time, date_time")
        .eq("office_id", office_id)
        .execute()
    )
    return response.data or []


@router.post("/", response_model=Event, status_code=status.HTTP_201_CREATED)
async def create_event(
    *,
    event_in: EventCreate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = event_in.model_dump(mode="json")
    payload["office_id"] = office_id
    response = supabase.table("events").insert(payload).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create event")
    return response.data[0]


@router.put("/{event_id}", response_model=Event)
async def update_event(
    *,
    event_id: str,
    event_in: EventUpdate,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    update_data = event_in.model_dump(mode="json", exclude_unset=True)
    update_data.pop("office_id", None)
    response = (
        supabase.table("events").update(update_data).eq("id", event_id).eq("office_id", office_id).execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Event not found")
    return response.data[0]


@router.delete("/{event_id}")
async def delete_event(
    *,
    event_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("events").delete().eq("id", event_id).eq("office_id", office_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"success": True}
