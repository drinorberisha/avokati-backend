"""Office (multi-tenant) scoping helpers.

The backend talks to Supabase with the service-role key, which bypasses RLS,
so tenant isolation on backend paths is enforced here in the app layer. Every
data endpoint resolves the caller's office via ``require_office`` and filters /
stamps ``office_id`` accordingly. (RLS — added in the Stage 3 migration — is the
parallel guard for the frontend's direct anon-key Supabase calls.)

See docs/BUILD_ORDER.md and docs/MULTI_TENANCY_PLAN.md.
"""

from fastapi import Depends, HTTPException, status

from app.core.auth import get_current_user
from app.schemas.user import User


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
    """Allow only office owners/admins (for member + invite management)."""
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
