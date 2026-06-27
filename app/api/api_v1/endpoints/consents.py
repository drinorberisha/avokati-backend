"""AI-processing consent endpoints (Compliance Phase 2).

Per-user consent for sending data to third-country LLM providers. The frontend
shows a one-time disclosure modal before first AvokAI / template-import use and
calls ``grant``; users can ``withdraw`` from Settings. ``ask-v2*`` and
``templates/extract`` enforce the same state server-side (see app/core/consent.py).
"""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.consent import AI_CONSENT_PURPOSE, AI_CONSENT_VERSION, get_ai_consent_row, is_ai_consented
from app.core.tenancy import require_office, get_user_supabase_client
from app.schemas.consent import ConsentStatusOut
from app.schemas.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


def _status(row: dict | None) -> ConsentStatusOut:
    return ConsentStatusOut(
        purpose=AI_CONSENT_PURPOSE,
        consented=is_ai_consented(row),
        current_version=AI_CONSENT_VERSION,
        consented_version=(row or {}).get("version"),
        granted_at=(row or {}).get("granted_at"),
        withdrawn_at=(row or {}).get("withdrawn_at"),
    )


@router.get("/ai-processing", response_model=ConsentStatusOut)
async def get_ai_consent_status(
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    row = get_ai_consent_row(supabase, str(current_user.id))
    return _status(row)


@router.post("/ai-processing/grant", response_model=ConsentStatusOut)
async def grant_ai_consent(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "user_id": str(current_user.id),
        "office_id": office_id,
        "purpose": AI_CONSENT_PURPOSE,
        "version": AI_CONSENT_VERSION,
        "granted_at": now,
        "withdrawn_at": None,
        "updated_at": now,
    }
    supabase.table("consents").upsert(record, on_conflict="user_id,purpose").execute()
    logger.info("AI-processing consent granted: user=%s version=%s", current_user.id, AI_CONSENT_VERSION)
    return _status(get_ai_consent_row(supabase, str(current_user.id)))


@router.post("/ai-processing/withdraw", response_model=ConsentStatusOut)
async def withdraw_ai_consent(
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_user_supabase_client),
) -> Any:
    now = datetime.now(timezone.utc).isoformat()
    (
        supabase.table("consents")
        .update({"withdrawn_at": now, "updated_at": now})
        .eq("user_id", str(current_user.id))
        .eq("purpose", AI_CONSENT_PURPOSE)
        .execute()
    )
    logger.info("AI-processing consent withdrawn: user=%s", current_user.id)
    return _status(get_ai_consent_row(supabase, str(current_user.id)))
