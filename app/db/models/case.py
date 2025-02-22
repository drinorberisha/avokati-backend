from sqlalchemy import Column, Text, DateTime, Enum as SQLEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from enum import Enum
from app.core.database import Base

class CaseStatus(str, Enum):
    open = "open"
    pending = "pending"
    closed = "closed"

class Case(Base):
    __tablename__ = "cases"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    case_number = Column(Text, nullable=False, unique=True)
    title = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    status = Column(SQLEnum(CaseStatus), nullable=False, server_default=CaseStatus.open.value)
    court = Column(Text, nullable=False)
    judge = Column(Text, nullable=False)
    next_hearing = Column(DateTime(timezone=True), nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    primary_attorney_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())

    # Relationships
    client = relationship("Client", back_populates="cases")
    primary_attorney = relationship("User", back_populates="primary_cases")
    documents = relationship("Document", back_populates="case") 