from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class LibraryDocumentOut(BaseModel):
    id: UUID
    title: str
    document_type: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    created_at: Optional[datetime] = None


class LibraryDownloadOut(BaseModel):
    url: str
