from sqlalchemy import Column, String, DateTime, Boolean, Integer, JSON, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from datetime import datetime

from app.db.base_class import Base


class LegalDocument(Base):
    """Model for legal documents."""
    __tablename__ = "legaldocument"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False, index=True)
    document_type = Column(String, nullable=False, index=True, default="other")
    status = Column(String, nullable=False, index=True, default="pending")
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    
    # S3 file fields
    file_key = Column(String, nullable=True)
    file_name = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    
    # Document state
    is_abolished = Column(Boolean, nullable=False, default=False)
    is_updated = Column(Boolean, nullable=False, default=False)
    parent_document_id = Column(String, ForeignKey("legaldocument.id"), nullable=True)
    parent_version_id = Column(String, ForeignKey("legal_document_version.id"), nullable=True)
    
    # Metadata and timestamps
    document_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    versions = relationship("LegalDocumentVersion", back_populates="document", foreign_keys="LegalDocumentVersion.document_id")
    parent_document = relationship("LegalDocument", remote_side=[id], backref="child_documents")
    parent_version = relationship("LegalDocumentVersion", foreign_keys=[parent_version_id])

    def __repr__(self):
        return f"<LegalDocument(id={self.id}, title={self.title}, type={self.document_type})>"


class LegalDocumentVersion(Base):
    """Model for legal document versions."""
    __tablename__ = "legal_document_version"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String, ForeignKey("legaldocument.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    file_key = Column(String, nullable=False)
    file_name = Column(String, nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String, nullable=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    changes_description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    document = relationship("LegalDocument", back_populates="versions", foreign_keys=[document_id]) 