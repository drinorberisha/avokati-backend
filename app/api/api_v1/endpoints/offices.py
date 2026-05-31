"""Office (tenant) management: create/rename, members, and invites.

Onboarding model (docs/BUILD_ORDER.md Stage 4): a freshly-signed-up user has
``office_id = NULL`` and must either create an office (becoming its owner) or
accept an invite to join an existing one. One office per user.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.supabase import get_supabase_client
from app.core.tenancy import require_office, require_office_admin
from app.schemas.office import (
    InviteCreate,
    InviteOut,
    InvitePreview,
    MemberUpdate,
    OfficeCreate,
    OfficeMemberOut,
    OfficeOut,
    OfficeUpdate,
)
from app.schemas.user import User

router = APIRouter()

INVITE_TTL_DAYS = 14


def _office_row(supabase, office_id: str) -> dict:
    resp = supabase.table("offices").select("*").eq("id", office_id).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Office not found")
    return resp.data[0]


@router.post("/", response_model=OfficeOut, status_code=status.HTTP_201_CREATED)
async def create_office(
    *,
    office_in: OfficeCreate,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    """Create an office and make the caller its owner. Onboarding entry point."""
    if getattr(current_user, "office_id", None):
        raise HTTPException(status_code=400, detail="You already belong to an office.")

    created = (
        supabase.table("offices")
        .insert({"name": office_in.name, "owner_id": str(current_user.id)})
        .execute()
    )
    if not created.data:
        raise HTTPException(status_code=400, detail="Failed to create office")
    office = created.data[0]

    supabase.table("users").update(
        {"office_id": office["id"], "office_role": "owner"}
    ).eq("id", str(current_user.id)).execute()

    return {**office, "role": "owner"}


@router.get("/me", response_model=OfficeOut)
async def get_my_office(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    office = _office_row(supabase, office_id)
    return {**office, "role": getattr(current_user, "office_role", "member")}


@router.patch("/me", response_model=OfficeOut)
async def update_my_office(
    *,
    office_in: OfficeUpdate,
    current_user: User = Depends(require_office_admin),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    update_data = office_in.model_dump(exclude_unset=True)
    if update_data:
        supabase.table("offices").update(update_data).eq("id", office_id).execute()
    office = _office_row(supabase, office_id)
    return {**office, "role": getattr(current_user, "office_role", "member")}


@router.get("/me/members", response_model=List[OfficeMemberOut])
async def list_members(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("users")
        .select("id, email, full_name, role, office_role, is_active")
        .eq("office_id", office_id)
        .execute()
    )
    return resp.data or []


@router.patch("/me/members/{user_id}", response_model=OfficeMemberOut)
async def update_member(
    *,
    user_id: str,
    member_in: MemberUpdate,
    current_user: User = Depends(require_office_admin),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    # Target must be a member of this office.
    target = (
        supabase.table("users")
        .select("id, office_role")
        .eq("id", user_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.data[0]["office_role"] == "owner":
        raise HTTPException(status_code=400, detail="The office owner cannot be modified.")

    update_data: dict = {}
    if member_in.office_role is not None:
        if member_in.office_role not in ("admin", "member"):
            raise HTTPException(status_code=400, detail="office_role must be 'admin' or 'member'.")
        update_data["office_role"] = member_in.office_role
    if member_in.is_active is not None:
        update_data["is_active"] = member_in.is_active
    if not update_data:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    supabase.table("users").update(update_data).eq("id", user_id).eq("office_id", office_id).execute()
    resp = (
        supabase.table("users")
        .select("id, email, full_name, role, office_role, is_active")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    return resp.data[0]


# ─── Invites ──────────────────────────────────────────────────────────────

@router.post("/me/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(
    *,
    invite_in: InviteCreate,
    current_user: User = Depends(require_office_admin),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    if invite_in.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'member'.")
    record = {
        "office_id": office_id,
        "email": invite_in.email,
        "token": secrets.token_urlsafe(32),
        "role": invite_in.role,
        "invited_by": str(current_user.id),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)).isoformat(),
    }
    resp = supabase.table("office_invites").insert(record).execute()
    if not resp.data:
        raise HTTPException(status_code=400, detail="Failed to create invite")
    return resp.data[0]


@router.get("/me/invites", response_model=List[InviteOut])
async def list_invites(
    current_user: User = Depends(require_office_admin),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("office_invites")
        .select("*")
        .eq("office_id", office_id)
        .is_("accepted_at", "null")
        .execute()
    )
    return resp.data or []


@router.delete("/me/invites/{invite_id}")
async def revoke_invite(
    *,
    invite_id: str,
    current_user: User = Depends(require_office_admin),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("office_invites")
        .delete()
        .eq("id", invite_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Invite not found")
    return {"success": True}


def _load_invite(supabase, token: str) -> dict:
    resp = supabase.table("office_invites").select("*").eq("token", token).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Invite not found")
    return resp.data[0]


def _is_expired(invite: dict) -> bool:
    if not invite.get("expires_at"):
        return False
    expires = datetime.fromisoformat(str(invite["expires_at"]).replace("Z", "+00:00"))
    return expires < datetime.now(timezone.utc)


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(
    *,
    token: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    invite = _load_invite(supabase, token)
    office = _office_row(supabase, invite["office_id"])
    return {
        "office_id": invite["office_id"],
        "office_name": office["name"],
        "role": invite["role"],
        "email": invite.get("email"),
        "expired": _is_expired(invite),
        "accepted": invite.get("accepted_at") is not None,
    }


@router.post("/invites/{token}/accept", response_model=OfficeOut)
async def accept_invite(
    *,
    token: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    if getattr(current_user, "office_id", None):
        raise HTTPException(status_code=400, detail="You already belong to an office.")

    invite = _load_invite(supabase, token)
    if invite.get("accepted_at") is not None:
        raise HTTPException(status_code=400, detail="This invite has already been used.")
    if _is_expired(invite):
        raise HTTPException(status_code=400, detail="This invite has expired.")
    # Email-targeted invites must match the authenticated user's email.
    if invite.get("email") and current_user.email and invite["email"].lower() != current_user.email.lower():
        raise HTTPException(status_code=403, detail="This invite is for a different email address.")

    supabase.table("users").update(
        {"office_id": invite["office_id"], "office_role": invite["role"]}
    ).eq("id", str(current_user.id)).execute()
    supabase.table("office_invites").update(
        {"accepted_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", invite["id"]).execute()

    office = _office_row(supabase, invite["office_id"])
    return {**office, "role": invite["role"]}
