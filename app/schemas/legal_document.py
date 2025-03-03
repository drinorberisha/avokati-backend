from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime


class LegalDocumentBase(BaseModel):
    """Base schema for legal document."""
    title: str
    content: str
    document_type: str
    document_metadata: Optional[Dict[str, Any]] = None
    is_abolished: bool = False
    is_updated: bool = False
    parent_document_id: Optional[str] = None


class LegalDocumentCreate(LegalDocumentBase):
    """Schema for creating a legal document."""
    pass


class LegalDocumentUpdate(BaseModel):
    """Schema for updating a legal document."""
    title: Optional[str] = None
    content: Optional[str] = None
    document_type: Optional[str] = None
    document_metadata: Optional[Dict[str, Any]] = None
    is_abolished: Optional[bool] = None
    is_updated: Optional[bool] = None
    parent_document_id: Optional[str] = None
    vector_id: Optional[str] = None


class LegalDocumentInDB(LegalDocumentBase):
    """Schema for a legal document in the database."""
    id: str
    vector_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LegalDocumentResponse(LegalDocumentInDB):
    """Schema for legal document response."""
    pass


class LegalDocumentSearchQuery(BaseModel):
    """Schema for legal document search query."""
    query: str = Field(..., description="The search query")
    document_type: Optional[str] = Field(None, description="Filter by document type")
    limit: int = Field(10, description="Number of results to return")


class LegalDocumentSearchResult(BaseModel):
    """Schema for legal document search result."""
    document: LegalDocumentResponse
    score: float = Field(..., description="Relevance score")
    
    
class LegalDocumentBatchCreate(BaseModel):
    """Schema for batch creating legal documents."""
    documents: List[LegalDocumentCreate] 