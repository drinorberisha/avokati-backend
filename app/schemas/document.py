from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, model_validator


class DocumentCategory(str, Enum):
    contract = "contract"
    court_filing = "court_filing"
    correspondence = "correspondence"
    evidence = "evidence"
    other = "other"


class DocumentBase(BaseModel):
    name: str
    category: DocumentCategory
    client_id: Optional[UUID] = None
    case_id: Optional[UUID] = None
    description: Optional[str] = None
    url: str

    @model_validator(mode="after")
    def has_one_association(self):
        if bool(self.client_id) == bool(self.case_id):
            raise ValueError("Document must be associated with exactly one client or one case")
        return self


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[DocumentCategory] = None
    client_id: Optional[UUID] = None
    case_id: Optional[UUID] = None
    description: Optional[str] = None
    url: Optional[str] = None


class Document(DocumentBase):
    id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentInDB(Document):
    pass


DocumentResponse = Document
