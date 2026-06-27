"""
CRUD operations for AvokAI chat sessions + messages.

Runs over the **RLS-bound user-JWT Supabase client** (PostgREST), NOT the
SQLAlchemy `postgres` connection — that role has BYPASSRLS, so the database
never enforced ownership there. With these calls going through the user's
token, the `chat_sessions_owner` / `chat_messages_owner` policies make Postgres
itself refuse cross-user access (see supabase/migrations/20260621_chat_rls.sql).
The `user_id` filters below are kept as defense-in-depth.

Each function takes the per-request user-scoped client (`get_user_supabase_client`)
and returns plain dicts shaped for the chat response schemas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 80
_DEFAULT_TITLE = "Bisedë e re"
_SESSION_COLS = "id, title, created_at, last_message_at"
_MESSAGE_COLS = "id, role, content, intent, sources, citations, abolishment_warnings, llm_usage, elapsed_ms, created_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_title_from_first_message(content: str) -> str:
    cleaned = (content or "").strip().replace("\n", " ")
    if not cleaned:
        return _DEFAULT_TITLE
    if len(cleaned) <= TITLE_MAX_CHARS:
        return cleaned
    return cleaned[: TITLE_MAX_CHARS - 1].rstrip() + "…"


async def list_sessions(supabase, user_id: UUID, limit: int = 100) -> list[dict]:
    """User's sessions, newest activity first. RLS scopes to the caller."""
    try:
        res = (
            supabase.table("chat_sessions")
            .select(_SESSION_COLS)
            .eq("user_id", str(user_id))
            .order("last_message_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("list_sessions failed: %s", e)
        return []


async def create_session(supabase, user_id: UUID, title: Optional[str] = None) -> Optional[dict]:
    """Create an empty session (title defaults to 'Bisedë e re' at the DB)."""
    try:
        payload: dict[str, Any] = {"user_id": str(user_id)}
        if title:
            payload["title"] = title[:200]
        res = supabase.table("chat_sessions").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error("create_session failed: %s", e)
        return None


async def get_session(
    supabase, session_id: UUID, user_id: UUID, *, with_messages: bool = False
) -> Optional[dict]:
    """Fetch a session (RLS-scoped to the owner). None if not owned/found."""
    try:
        sel = _SESSION_COLS
        if with_messages:
            sel = f"{_SESSION_COLS}, messages:chat_messages({_MESSAGE_COLS})"
        res = (
            supabase.table("chat_sessions")
            .select(sel)
            .eq("id", str(session_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]
        if with_messages:
            row["messages"] = sorted(
                row.get("messages") or [], key=lambda m: m.get("created_at") or ""
            )
        return row
    except Exception as e:
        logger.error("get_session failed: %s", e)
        return None


async def update_session_title(
    supabase, session_id: UUID, user_id: UUID, title: str
) -> Optional[dict]:
    """Rename a session (no update trigger exists → stamp updated_at here)."""
    try:
        res = (
            supabase.table("chat_sessions")
            .update({"title": title[:200], "updated_at": _now()})
            .eq("id", str(session_id))
            .eq("user_id", str(user_id))
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error("update_session_title failed: %s", e)
        return None


async def delete_session(supabase, session_id: UUID, user_id: UUID) -> bool:
    """Hard-delete a session; messages cascade via FK. True on success."""
    try:
        res = (
            supabase.table("chat_sessions")
            .delete()
            .eq("id", str(session_id))
            .eq("user_id", str(user_id))
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error("delete_session failed: %s", e)
        return False


async def append_turn(
    supabase,
    *,
    session_id: UUID,
    user_id: UUID,
    user_content: str,
    assistant_content: str,
    intent: Optional[str] = None,
    sources: Optional[list[dict[str, Any]]] = None,
    citations: Optional[list[dict[str, Any]]] = None,
    abolishment_warnings: Optional[list[str]] = None,
    llm_usage: Optional[dict[str, Any]] = None,
    elapsed_ms: Optional[int] = None,
    auto_title_if_empty: bool = True,
) -> bool:
    """Persist one user→assistant exchange and bump the session activity time.

    Ownership is enforced by RLS: the session lookup returns nothing if the
    caller doesn't own it, and the message-insert policy checks the parent
    session's owner. Three PostgREST calls (no cross-statement transaction) —
    acceptable since chat persistence is best-effort.
    """
    sess = (
        supabase.table("chat_sessions")
        .select("id, title")
        .eq("id", str(session_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    ).data
    if not sess:
        logger.warning("append_turn: session %s not owned by user %s", session_id, user_id)
        return False

    try:
        new_title: Optional[str] = None
        if auto_title_if_empty and sess[0].get("title") == _DEFAULT_TITLE:
            existing = (
                supabase.table("chat_messages")
                .select("id")
                .eq("session_id", str(session_id))
                .limit(1)
                .execute()
            ).data
            if not existing:
                new_title = _make_title_from_first_message(user_content)

        supabase.table("chat_messages").insert([
            {"session_id": str(session_id), "role": "user", "content": user_content},
            {
                "session_id": str(session_id),
                "role": "assistant",
                "content": assistant_content,
                "intent": intent,
                "sources": sources,
                "citations": citations,
                "abolishment_warnings": abolishment_warnings,
                "llm_usage": llm_usage,
                "elapsed_ms": elapsed_ms,
            },
        ]).execute()

        bump: dict[str, Any] = {"last_message_at": _now(), "updated_at": _now()}
        if new_title:
            bump["title"] = new_title
        supabase.table("chat_sessions").update(bump).eq("id", str(session_id)).eq("user_id", str(user_id)).execute()
        return True
    except Exception as e:
        logger.error("append_turn failed: %s", e)
        return False


__all__ = [
    "list_sessions",
    "create_session",
    "get_session",
    "update_session_title",
    "delete_session",
    "append_turn",
]
