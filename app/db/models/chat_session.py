from sqlalchemy import Column, Text, DateTime, ForeignKey, CheckConstraint, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.core.database import Base


class ChatSession(Base):
    """A persisted AvokAI chat session.

    Sessions are private per-user. Title is auto-generated from the first
    user message (truncated to ~80 chars) and editable by the user later.
    `last_message_at` drives sidebar ordering.
    """

    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False, server_default="Bisedë e re")
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)
    last_message_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """One turn in a chat session.

    Assistant messages carry the rich AvokAI payload (sources, citations,
    abolishment warnings, LLM usage, latency) so reloading a session can
    re-render the same sidebar / badges the user saw originally.
    """

    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)

    intent = Column(Text, nullable=True)
    sources = Column(JSONB, nullable=True)
    citations = Column(JSONB, nullable=True)
    abolishment_warnings = Column(ARRAY(Text), nullable=True)
    llm_usage = Column(JSONB, nullable=True)
    elapsed_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="chat_messages_role_check"),
    )

    session = relationship("ChatSession", back_populates="messages")


__all__ = ["ChatSession", "ChatMessage"]
