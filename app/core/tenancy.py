"""Office (multi-tenant) scoping helpers.

The backend talks to Supabase with the service-role key, which bypasses RLS,
so tenant isolation on backend paths is enforced here in the app layer. Every
data endpoint resolves the caller's office via ``require_office`` and filters /
stamps ``office_id`` accordingly. (RLS — added in the Stage 3 migration — is the
parallel guard for the frontend's direct anon-key Supabase calls.)

See docs/BUILD_ORDER.md and docs/MULTI_TENANCY_PLAN.md.
"""

import logging

from fastapi import Depends, HTTPException, status

from app.core.auth import get_current_user, oauth2_scheme
from app.core.supabase import get_user_client
from app.schemas.user import User

logger = logging.getLogger(__name__)


def get_user_supabase_client(token: str = Depends(oauth2_scheme)):
    """RLS-enforcing Supabase client bound to the calling user.

    Use this for ALL per-office data endpoints instead of the service-role
    ``get_supabase_client`` — it makes Postgres RLS (not app-layer ``.eq``
    discipline) the thing that refuses cross-office access. The manual
    ``office_id`` filters stay as defense-in-depth. See docs/PRODUCT_ROADMAP.md P1.
    """
    return get_user_client(token)


def assert_office_scoped(rows, office_id: str, *, where: str = ""):
    """Canary: every returned row's ``office_id`` MUST equal the request's.

    RLS already guarantees this; the canary turns a future regression (someone
    reverts to the service-role client, a policy gets dropped) into a LOUD log
    line instead of a silent cross-tenant leak. Rows that don't carry
    ``office_id`` in their projection are skipped. Returns ``rows`` unchanged.
    """
    leaked = [
        r.get("id")
        for r in (rows or [])
        if r.get("office_id") is not None and str(r.get("office_id")) != str(office_id)
    ]
    if leaked:
        logger.error(
            "TENANCY CANARY TRIPPED at %s: rows outside office %s leaked (ids=%s)",
            where or "?", office_id, leaked[:5],
        )
    return rows


def require_office(current_user: User = Depends(get_current_user)) -> str:
    """Return the caller's office_id (as str), or 403 if they haven't onboarded.

    A freshly-signed-up user has ``office_id = NULL`` until they create or join
    an office; data endpoints must not serve them the (now empty) global view.
    The typed ``NEEDS_ONBOARDING`` code lets the frontend route to onboarding.
    """
    office_id = getattr(current_user, "office_id", None)
    if not office_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "NEEDS_ONBOARDING",
                "message": "User is not assigned to an office.",
            },
        )
    return str(office_id)


def require_office_admin(current_user: User = Depends(get_current_user)) -> User:
    """Allow only office owners/admins (e.g. for invites)."""
    if not getattr(current_user, "office_id", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NEEDS_ONBOARDING", "message": "User is not assigned to an office."},
        )
    if getattr(current_user, "office_role", "member") not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires office admin privileges.",
        )
    return current_user


def require_office_owner(current_user: User = Depends(get_current_user)) -> User:
    """Allow only the office owner (the creator). Used for changing member
    roles — that power is reserved to the owner alone."""
    if not getattr(current_user, "office_id", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NEEDS_ONBOARDING", "message": "User is not assigned to an office."},
        )
    if getattr(current_user, "office_role", "member") != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the office owner can change member roles.",
        )
    return current_user


def assert_in_office(supabase, table: str, row_id, office_id: str, *, detail: str = "Not found") -> None:
    """404 unless ``row_id`` exists in ``table`` within ``office_id``.

    Used to verify a parent reference (e.g. a case's client_id) belongs to the
    caller's office before linking to it, preventing cross-office references.
    """
    if row_id is None:
        return
    resp = (
        supabase.table(table)
        .select("id")
        .eq("id", str(row_id))
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
