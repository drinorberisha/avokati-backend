from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.base_class import Base


class LegalDocument(Base):
    """
    Model for legal documents that will be used for AI retrieval.
    """
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, index=True, nullable=False)
    content = Column(Text, nullable=False)
    document_type = Column(String, index=True, nullable=False)  # e.g., law, regulation, court decision
    document_metadata = Column(JSON, nullable=True)  # Store additional metadata like publication date, source, etc.
    vector_id = Column(String, nullable=True)  # ID in the vector database (Pinecone)
    is_abolished = Column(Boolean, default=False)  # Whether the law has been abolished
    is_updated = Column(Boolean, default=False)  # Whether the law has been updated
    parent_document_id = Column(String, ForeignKey("legaldocument.id"), nullable=True)  # For updated versions
    
    # Relationships
    child_documents = relationship("LegalDocument", 
                                  backref="parent_document",
                                  remote_side=[id],
                                  cascade="all, delete-orphan")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<LegalDocument(id={self.id}, title={self.title}, type={self.document_type})>" 