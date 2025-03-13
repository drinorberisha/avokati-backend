from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.base_class import Base


class LegalDocument(Base):
    """
    Model for legal documents that will be used for AI retrieval.
    """
    __tablename__ = "legaldocument"
    
    id = Column(
        UUID(as_uuid=False), 
        primary_key=True, 
        index=True, 
        default=lambda: str(uuid.uuid4())
    )
    title = Column(String, index=True, nullable=False)
    content = Column(Text, nullable=True)  # Content might not be available during initial upload
    document_type = Column(String, index=True, nullable=False)  # e.g., law, regulation, court decision
    status = Column(String, index=True, nullable=False, default="pending")  # Status of document processing
    document_metadata = Column(JSON, nullable=True)  # Store additional metadata like publication date, source, etc.
    vector_id = Column(String, nullable=True)  # ID in the vector database (Pinecone)
    is_abolished = Column(Boolean, default=False)  # Whether the law has been abolished
    is_updated = Column(Boolean, default=False)  # Whether the law has been updated
    is_annex = Column(Boolean, default=False)  # Whether this is an annex law (addition to another law)
    user_id = Column(String, nullable=True)  # ID of the user who uploaded the document
    
    # S3 storage fields
    file_key = Column(String, nullable=True)  # S3 key for the file
    file_name = Column(String, nullable=True)  # Original filename
    file_size = Column(Integer, nullable=True)  # File size in bytes
    mime_type = Column(String, nullable=True)  # MIME type of the file
    version = Column(Integer, nullable=False, default=1)  # Current version number
    
    # Relationships
    versions = relationship(
        "LegalDocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="desc(LegalDocumentVersion.version_number)"
    )
    articles = relationship(
        "LegalDocumentArticle",
        back_populates="document",
        cascade="all, delete-orphan"
    )
    annotations = relationship(
        "LegalDocumentAnnotation",
        back_populates="document",
        cascade="all, delete-orphan"
    )
    
    # Parent-child relationship for document hierarchy
    parent_document_id = Column(UUID(as_uuid=False), ForeignKey("legaldocument.id"), nullable=True)
    child_documents = relationship(
        "LegalDocument",
        backref="parent_document",
        remote_side=[id],
        cascade="all, delete-orphan",
        single_parent=True
    )
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<LegalDocument(id={self.id}, title={self.title}, type={self.document_type})>"

    @property
    def current_version(self):
        """Get the current version of the document."""
        return self.versions[0] if self.versions else None


class LegalDocumentVersion(Base):
    """
    Model for tracking versions of legal documents.
    """
    __tablename__ = "legal_document_version"
    
    id = Column(UUID(as_uuid=False), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        UUID(as_uuid=False), 
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    version_number = Column(Integer, nullable=False, index=True)
    file_key = Column(String, nullable=False)  # S3 key for this version
    file_name = Column(String, nullable=False)  # Original filename
    file_size = Column(Integer, nullable=False)  # File size in bytes
    mime_type = Column(String, nullable=False)  # MIME type of the file
    changes_description = Column(Text, nullable=True)  # Description of changes in this version
    created_by_id = Column(String, nullable=False)  # User who created this version
    
    # Relationship back to the document
    document = relationship(
        "LegalDocument",
        back_populates="versions",
        foreign_keys=[document_id]
    )
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<LegalDocumentVersion(id={self.id}, document_id={self.document_id}, version={self.version_number})>"


class LegalDocumentArticle(Base):
    """
    Model for articles within legal documents.
    """
    __tablename__ = "legal_document_article"
    
    id = Column(UUID(as_uuid=False), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    article_number = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    is_abolished = Column(Boolean, default=False)
    is_amended = Column(Boolean, default=False)
    effective_date = Column(DateTime(timezone=True), nullable=True)
    abolishment_date = Column(DateTime(timezone=True), nullable=True)
    document_metadata = Column(JSON, nullable=True)
    
    # Relationships
    document = relationship("LegalDocument", back_populates="articles")
    amendments = relationship(
        "LegalDocumentArticleAmendment",
        back_populates="article",
        cascade="all, delete-orphan"
    )
    annotations = relationship(
        "LegalDocumentAnnotation",
        back_populates="article",
        cascade="all, delete-orphan"
    )
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class LegalDocumentRelationship(Base):
    """
    Model for relationships between legal documents.
    """
    __tablename__ = "legal_document_relationship"
    
    id = Column(UUID(as_uuid=False), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    source_document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    target_document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    relationship_type = Column(String, nullable=False, index=True)
    document_metadata = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class LegalDocumentCitation(Base):
    """
    Model for citations between legal documents.
    """
    __tablename__ = "legal_document_citation"
    
    id = Column(UUID(as_uuid=False), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    source_document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    cited_document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    citation_text = Column(Text, nullable=True)
    location_in_source = Column(String, nullable=True)
    document_metadata = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class LegalDocumentAnnotation(Base):
    """
    Model for annotations on legal documents or articles.
    """
    __tablename__ = "legal_document_annotation"
    
    id = Column(UUID(as_uuid=False), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    article_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legal_document_article.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    annotation_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    location_in_document = Column(String, nullable=True)
    document_metadata = Column(JSON, nullable=True)
    created_by = Column(String, nullable=False)
    
    # Relationships
    document = relationship("LegalDocument", back_populates="annotations")
    article = relationship("LegalDocumentArticle", back_populates="annotations")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class LegalDocumentArticleAmendment(Base):
    """
    Model for amendments to legal document articles.
    """
    __tablename__ = "legal_document_article_amendment"
    
    id = Column(UUID(as_uuid=False), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    article_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legal_document_article.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    amendment_type = Column(String, nullable=False)
    previous_content = Column(Text, nullable=True)
    new_content = Column(Text, nullable=True)
    amendment_date = Column(DateTime(timezone=True), nullable=False)
    effective_date = Column(DateTime(timezone=True), nullable=True)
    amendment_source_id = Column(
        UUID(as_uuid=False),
        ForeignKey("legaldocument.id", ondelete="SET NULL"),
        nullable=True
    )
    document_metadata = Column(JSON, nullable=True)
    
    # Relationships
    article = relationship("LegalDocumentArticle", back_populates="amendments")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now()) 