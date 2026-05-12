"""
Pydantic schemas for AvokAI chat session persistence.

Shapes mirror the SQLAlchemy ChatSession / ChatMessage models, plus a
SessionWithMessages for the detail-view endpoint that returns a session
and all its messages in one round-trip.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class ChatSessionBase(BaseModel):
    title: str = Field(..., max_length=200)


class ChatSessionCreate(BaseModel):
    """Optional body for explicit session creation. Both fields default."""
    title: Optional[str] = Field(None, max_length=200)


class ChatSessionUpdate(BaseModel):
    title: str = Field(..., max_length=200)


class ChatSessionSummary(BaseModel):
    """One row in the sidebar list — minimal payload."""
    id: UUID
    title: str
    created_at: datetime
    last_message_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    intent: Optional[str] = None
    sources: Optional[list[dict[str, Any]]] = None
    citations: Optional[list[dict[str, Any]]] = None
    abolishment_warnings: Optional[list[str]] = None
    llm_usage: Optional[dict[str, Any]] = None
    elapsed_ms: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionDetail(BaseModel):
    """Session + all its messages, for the /sessions/{id} GET endpoint."""
    id: UUID
    title: str
    created_at: datetime
    last_message_at: datetime
    messages: List[ChatMessageOut]

    model_config = {"from_attributes": True}


__all__ = [
    "ChatSessionCreate",
    "ChatSessionUpdate",
    "ChatSessionSummary",
    "ChatSessionDetail",
    "ChatMessageOut",
]
