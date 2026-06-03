"""AI-processing consent gate (Compliance Phase 2).

AvokAI sends queries + context to OpenAI (US) and DeepSeek (China); template
OCR sends uploaded documents to Gemini (US). Before any such transfer, the user
must give explicit, informed consent (Kosovo Law 06/L-082 / GDPR Art. 49). This
module is the backend enforcement point — a defense-in-depth guard that the
frontend consent modal also fronts.

Bump ``AI_CONSENT_VERSION`` whenever the disclosure text changes materially:
older grants then read as not-consented and the user is re-prompted.
"""

import logging

from fastapi import Depends, HTTPException, status

from app.core.auth import get_current_user
from app.core.supabase import get_supabase_client
from app.schemas.user import User

logger = logging.getLogger(__name__)

AI_CONSENT_PURPOSE = "ai_processing"
AI_CONSENT_VERSION = "2026-06-v1"


def get_ai_consent_row(supabase, user_id: str) -> dict | None:
    """Return the caller's current ai_processing consent row, or None."""
    resp = (
        supabase.table("consents")
        .select("version, granted_at, withdrawn_at")
        .eq("user_id", user_id)
        .eq("purpose", AI_CONSENT_PURPOSE)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def is_ai_consented(row: dict | None) -> bool:
    """True only if consent is granted, not withdrawn, and for the current version."""
    if not row:
        return False
    return row.get("withdrawn_at") is None and row.get("version") == AI_CONSENT_VERSION


def require_ai_consent(
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> None:
    """FastAPI dependency: 403 CONSENT_REQUIRED unless the caller has granted
    current-version AI-processing consent. Attach to every endpoint that sends
    personal data to a third-country LLM provider."""
    row = get_ai_consent_row(supabase, str(current_user.id))
    if not is_ai_consented(row):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": "AI-processing consent is required before using this feature.",
                "version": AI_CONSENT_VERSION,
            },
        )
