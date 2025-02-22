from sqlalchemy import Column, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base

class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    version_number = Column(Integer, nullable=False)
    file_key = Column(Text, nullable=False)
    file_name = Column(Text, nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(Text, nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('document_id', 'version_number', name='uq_document_version'),
    )

    document = relationship("Document", back_populates="versions")
    created_by = relationship("User", back_populates="created_versions") 