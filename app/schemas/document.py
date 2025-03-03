from enum import Enum
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, HttpUrl
from .base import BaseSchema

class DocumentStatus(str, Enum):
    draft = "draft"
    final = "final"
    archived = "archived"

class CollaboratorRole(str, Enum):
    viewer = "viewer"
    editor = "editor"
    owner = "owner"

class DocumentBase(BaseModel):
    title: str
    type: str
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
    pass

class DocumentUpdate(DocumentBase):
    title: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    status: Optional[DocumentStatus] = None
    file_key: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    tags: Optional[List[str]] = None
    case_id: Optional[UUID] = None
    client_id: Optional[UUID] = None

class Document(DocumentBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class DocumentInDB(Document):
    """Database representation of a document, with any additional DB-specific fields."""
    pass

class DocumentResponse(Document):
    download_url: Optional[HttpUrl] = None
    versions: Optional[List['DocumentVersionResponse']] = None

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