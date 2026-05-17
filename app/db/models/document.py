from sqlalchemy import Column, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from enum import Enum
from app.core.database import Base

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    url = Column(Text, nullable=False)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.id"), nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())

    # Ensure document is associated with either a case or a client, but not both
    __table_args__ = (
        CheckConstraint(
            'case_id IS NOT NULL AND client_id IS NULL OR case_id IS NULL AND client_id IS NOT NULL',
            name='document_case_or_client'
        ),
    )

    # Relationships
    case = relationship("Case", back_populates="documents")
    client = relationship("Client", back_populates="documents")
