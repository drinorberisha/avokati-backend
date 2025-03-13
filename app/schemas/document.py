from enum import Enum
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, HttpUrl, Field
from .base import BaseSchema
import uuid

class DocumentStatus(str, Enum):
    draft = "draft"
    final = "final"
    archived = "archived"

class CollaboratorRole(str, Enum):
    viewer = "viewer"
    editor = "editor"
    owner = "owner"

class DocumentBase(BaseModel):
    """Base document schema."""
    title: str
    document_type: str = Field(
        default="other", 
        description="Type of document (law, regulation, case_law, contract, article, other)"
    )
    category: str
    status: DocumentStatus = DocumentStatus.draft
    file_key: str  # S3 file key
    file_name: str
    file_size: int
    mime_type: str
    version: int = 1
    tags: List[str] = []
    case_id: Optional[UUID] = None
    client_id: Optional[UUID] = None

class DocumentCreate(DocumentBase):
    """Schema for creating a document."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    status: str = Field(
        default="pending", 
        description="Status of document processing (pending, processing, processed, failed)"
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class DocumentUpdate(BaseModel):
    """Schema for updating a document."""
    title: Optional[str] = None
    document_type: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)
    file_key: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    tags: Optional[List[str]] = None
    case_id: Optional[UUID] = None
    client_id: Optional[UUID] = None
    metadata: Optional[Dict[str, Any]] = None

class Document(DocumentBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class DocumentInDB(Document):
    """Database representation of a document, with any additional DB-specific fields."""
    pass

class DocumentResponse(DocumentBase):
    """Schema for document response."""
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        orm_mode = True

class DocumentList(BaseModel):
    """Schema for listing documents."""
    id: str
    title: str
    document_type: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class DocumentContent(BaseModel):
    """Schema for document content."""
    id: str
    title: str
    content: str
    document_type: str
    document_metadata: Dict[str, Any] = Field(default_factory=dict)

class DocumentSearchResult(BaseModel):
    """Schema for document search result."""
    id: str
    title: str
    content: str
    document_type: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DocumentSearchResponse(BaseModel):
    """Schema for document search response."""
    query: str
    results: List[DocumentSearchResult]
    total: int

class DocumentVersionBase(BaseModel):
    document_id: UUID
    version_number: int
    file_key: str
    file_name: str
    file_size: int
    mime_type: str
    created_by_id: UUID
    changes_description: Optional[str] = None

class DocumentVersionCreate(DocumentVersionBase):
    pass

class DocumentVersion(DocumentVersionBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class DocumentVersionResponse(DocumentVersion):
    download_url: Optional[HttpUrl] = None

class DocumentCollaboratorBase(BaseModel):
    document_id: UUID
    user_id: UUID
    role: CollaboratorRole

class DocumentCollaboratorCreate(DocumentCollaboratorBase):
    pass

class DocumentCollaborator(DocumentCollaboratorBase):
    id: UUID
    added_at: datetime = datetime.utcnow()
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True 