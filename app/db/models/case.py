from sqlalchemy import Column, Text, ForeignKey, CheckConstraint
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
    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, server_default='open')
    court = Column(Text, nullable=True)
    judge = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)

    # Add check constraint to ensure valid status values
    __table_args__ = (
        CheckConstraint(
            status.in_(['open', 'pending', 'closed']),
            name='check_valid_case_status'
        ),
    )

    # Relationships
    client = relationship("Client", back_populates="cases")
    documents = relationship("Document", back_populates="case") 
    milestones = relationship("CaseMilestone", back_populates="case", cascade="all, delete-orphan")
