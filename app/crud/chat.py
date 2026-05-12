"""
CRUD operations for AvokAI chat sessions + messages.

All operations scope by `user_id` to enforce per-user privacy — callers
pass the authenticated user's ID and any session not owned by them is
treated as not-found.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.db.models.chat_session import ChatSession, ChatMessage

logger = logging.getLogger(__name__)

# How many chars of the first user message become the auto-generated title.
TITLE_MAX_CHARS = 80


def _make_title_from_first_message(content: str) -> str:
    """Truncate user's first message to a sidebar-friendly title."""
    cleaned = (content or "").strip().replace("\n", " ")
    if not cleaned:
        return "Bisedë e re"
    if len(cleaned) <= TITLE_MAX_CHARS:
        return cleaned
    return cleaned[: TITLE_MAX_CHARS - 1].rstrip() + "…"


async def list_sessions(db: AsyncSession, user_id: UUID, limit: int = 100) -> list[ChatSession]:
    """Return user's sessions, newest activity first."""
    try:
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.last_message_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    except SQLAlchemyError as e:
        logger.error("list_sessions failed: %s", e)
        return []


async def create_session(db: AsyncSession, user_id: UUID, title: Optional[str] = None) -> Optional[ChatSession]:
    """Create an empty session. Title can be set explicitly or left as default."""
    try:
        session = ChatSession(user_id=user_id)
        if title:
            session.title = title[:200]
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("create_session failed: %s", e)
        return None


async def get_session(
    db: AsyncSession, session_id: UUID, user_id: UUID, *, with_messages: bool = False
) -> Optional[ChatSession]:
    """Fetch a session, scoped to the owning user. Returns None if not owned."""
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
        )
        if with_messages:
            stmt = stmt.options(selectinload(ChatSession.messages))
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
    except SQLAlchemyError as e:
        logger.error("get_session failed: %s", e)
        return None


async def update_session_title(
    db: AsyncSession, session_id: UUID, user_id: UUID, title: str
) -> Optional[ChatSession]:
    """Rename a session."""
    session = await get_session(db, session_id, user_id)
    if session is None:
        return None
    try:
        session.title = title[:200]
        session.updated_at = func.current_timestamp()
        await db.commit()
        await db.refresh(session)
        return session
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("update_session_title failed: %s", e)
        return None


async def delete_session(db: AsyncSession, session_id: UUID, user_id: UUID) -> bool:
    """Hard-delete a session (cascade deletes messages). Returns True on success."""
    try:
        result = await db.execute(
            delete(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        )
        await db.commit()
        return result.rowcount > 0
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error("delete_session failed: %s", e)
        return False


async def append_turn(
    db: AsyncSession,
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
    """Persist one user→assistant exchange and bump the session's activity timestamp.

    If the session is empty and `auto_title_if_empty` is True, the title is
    set to the first ~80 chars of `user_content` so the sidebar gets a useful
    label instead of "Bisedë e re".

    Returns True on success.
    """
    session = await get_session(db, session_id, user_id, with_messages=False)
    if session is None:
        logger.warning("append_turn: session %s not found for user %s", session_id, user_id)
        return False

    try:
        if auto_title_if_empty and session.title == "Bisedë e re":
            # Check whether any messages exist already; if not, set title.
            existing_count = await db.execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.session_id == session_id)
            )
            if (existing_count.scalar() or 0) == 0:
                session.title = _make_title_from_first_message(user_content)

        user_msg = ChatMessage(
            session_id=session_id,
            role="user",
            content=user_content,
        )
        assistant_msg = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            intent=intent,
            sources=sources,
            citations=citations,
            abolishment_warnings=abolishment_warnings,
            llm_usage=llm_usage,
            elapsed_ms=elapsed_ms,
        )
        db.add(user_msg)
        db.add(assistant_msg)

        session.last_message_at = func.current_timestamp()
        session.updated_at = func.current_timestamp()

        await db.commit()
        return True
    except SQLAlchemyError as e:
        await db.rollback()
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
