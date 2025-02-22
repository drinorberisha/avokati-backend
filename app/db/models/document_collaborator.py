from sqlalchemy import Column, DateTime, Enum as SQLEnum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from enum import Enum
from app.core.database import Base

class CollaboratorRole(str, Enum):
    viewer = "viewer"
    editor = "editor"
    owner = "owner"

class DocumentCollaborator(Base):
    __tablename__ = "document_collaborators"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role = Column(SQLEnum(CollaboratorRole), nullable=False)
    added_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())

    # Unique constraint for document_id and user_id
    __table_args__ = (
        UniqueConstraint('document_id', 'user_id', name='document_collaborators_document_id_user_id_key'),
    )

    # Relationships
    document = relationship("Document", back_populates="collaborators")
    user = relationship("User", back_populates="document_collaborations") 