from typing import Optional, Dict, Any, List, Union
from pydantic import BaseModel, Field, field_validator, model_validator, UUID4
from datetime import datetime
from enum import Enum


class DocumentType(str, Enum):
    LAW = "law"
    REGULATION = "regulation"
    CASE_LAW = "case_law"
    CONTRACT = "contract"
    ARTICLE = "article"
    OTHER = "other"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    ARCHIVED = "archived"


class LegalDocumentVersionBase(BaseModel):
    document_id: UUID4
    version_number: int
    file_key: str
    file_name: str
    file_size: int
    mime_type: str
    changes_description: Optional[str] = None


class LegalDocumentVersionCreate(LegalDocumentVersionBase):
    created_by_id: UUID4


class LegalDocumentVersion(LegalDocumentVersionBase):
    id: UUID4
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LegalDocumentBase(BaseModel):
    title: str
    document_type: DocumentType
    content: Optional[str] = None
    document_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    is_abolished: bool = False
    is_updated: bool = False
    is_annex: bool = False
    file_key: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    parent_document_id: Optional[UUID4] = None


class LegalDocumentCreate(LegalDocumentBase):
    user_id: Optional[str] = None
    status: DocumentStatus = DocumentStatus.PENDING

    @model_validator(mode='before')
    @classmethod
    def validate_model(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(data, dict) and 'id' in data:
            if isinstance(data['id'], UUID4):
                data['id'] = str(data['id'])
        return data

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        data = super().model_dump(**kwargs)
        if data.get('id') and isinstance(data['id'], UUID4):
            data['id'] = str(data['id'])
        return data


class LegalDocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    document_type: Optional[DocumentType] = None
    document_metadata: Optional[Dict[str, Any]] = None
    is_abolished: Optional[bool] = None
    is_updated: Optional[bool] = None
    is_annex: Optional[bool] = None
    status: Optional[DocumentStatus] = None
    parent_document_id: Optional[UUID4] = None


class LegalDocumentArticleBase(BaseModel):
    document_id: UUID4
    article_number: str
    title: Optional[str] = None
    content: str
    is_abolished: bool = False
    is_amended: bool = False
    effective_date: Optional[datetime] = None
    abolishment_date: Optional[datetime] = None
    document_metadata: Dict[str, Any] = Field(default_factory=dict)


class LegalDocumentArticleCreate(LegalDocumentArticleBase):
    pass


class LegalDocumentArticleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    is_abolished: Optional[bool] = None
    is_amended: Optional[bool] = None
    effective_date: Optional[datetime] = None
    abolishment_date: Optional[datetime] = None
    document_metadata: Optional[Dict[str, Any]] = None


class LegalDocumentRelationshipBase(BaseModel):
    source_document_id: UUID4
    target_document_id: UUID4
    relationship_type: str
    document_metadata: Dict[str, Any] = Field(default_factory=dict)


class LegalDocumentRelationshipCreate(LegalDocumentRelationshipBase):
    pass


class LegalDocumentCitationBase(BaseModel):
    source_document_id: UUID4
    cited_document_id: UUID4
    citation_text: Optional[str] = None
    location_in_source: Optional[str] = None
    document_metadata: Dict[str, Any] = Field(default_factory=dict)


class LegalDocumentCitationCreate(LegalDocumentCitationBase):
    pass


class LegalDocumentAnnotationBase(BaseModel):
    document_id: UUID4
    article_id: Optional[UUID4] = None
    annotation_type: str
    content: str
    location_in_document: Optional[str] = None
    document_metadata: Dict[str, Any] = Field(default_factory=dict)


class LegalDocumentAnnotationCreate(LegalDocumentAnnotationBase):
    created_by: UUID4


class LegalDocumentArticleAmendmentBase(BaseModel):
    article_id: UUID4
    amendment_type: str
    previous_content: Optional[str] = None
    new_content: Optional[str] = None
    amendment_date: datetime
    effective_date: Optional[datetime] = None
    amendment_source_id: Optional[UUID4] = None
    document_metadata: Dict[str, Any] = Field(default_factory=dict)


class LegalDocumentArticleAmendmentCreate(LegalDocumentArticleAmendmentBase):
    pass


class LegalDocumentArticle(LegalDocumentArticleBase):
    id: UUID4
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LegalDocumentRelationship(LegalDocumentRelationshipBase):
    id: UUID4
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LegalDocumentCitation(LegalDocumentCitationBase):
    id: UUID4
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LegalDocumentAnnotation(LegalDocumentAnnotationBase):
    id: UUID4
    created_by: UUID4
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LegalDocumentArticleAmendment(LegalDocumentArticleAmendmentBase):
    id: UUID4
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LegalDocument(LegalDocumentBase):
    id: UUID4
    status: DocumentStatus
    version: int = 1
    vector_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    user_id: Optional[str] = None
    versions: List[LegalDocumentVersion] = []
    articles: List[LegalDocumentArticle] = []
    annotations: List[LegalDocumentAnnotation] = []

    class Config:
        from_attributes = True


class LegalDocumentInDB(LegalDocumentBase):
    """Schema for legal document in database."""
    id: str
    user_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    document_metadata: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
        # Keep orm_mode for backward compatibility
        orm_mode = True


class LegalDocumentResponse(LegalDocumentBase):
    """Schema for legal document response."""
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    document_metadata: Optional[Dict[str, Any]] = None
    message: Optional[str] = None

    class Config:
        from_attributes = True
        # Keep orm_mode for backward compatibility
        orm_mode = True


class LegalDocumentList(BaseModel):
    """Schema for listing legal documents."""
    id: str
    title: str
    document_type: str
    status: str
    file_name: Optional[str]
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        # Keep orm_mode for backward compatibility
        orm_mode = True


class LegalDocumentContent(BaseModel):
    """Schema for legal document content."""
    id: str
    title: str
    content: str
    document_type: str
    document_metadata: Dict[str, Any] = Field(default_factory=dict)


class LegalDocumentSearchResult(BaseModel):
    """Schema for legal document search result."""
    document: LegalDocumentResponse
    score: float


class LegalDocumentSearchResponse(BaseModel):
    """Schema for legal document search response."""
    query: str
    results: List[LegalDocumentSearchResult]
    total: int


class LegalDocumentSearchQuery(BaseModel):
    """Schema for legal document search query."""
    query: str
    document_type: Optional[DocumentType] = None
    include_abolished: bool = False
    limit: int = 10
    offset: int = 0


class LegalDocumentBatchCreate(BaseModel):
    """Schema for batch creating legal documents."""
    documents: List[LegalDocumentCreate]


class LegalDocumentVersion(BaseModel):
    """Schema for legal document versions."""
    id: str = Field(default_factory=lambda: str(UUID4()))
    document_id: str
    version_number: int
    file_key: str
    file_name: str
    file_size: int
    mime_type: str
    created_by_id: str
    changes_description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Config:
        from_attributes = True


class LegalDocumentVersionCreate(BaseModel):
    """Schema for creating a legal document version."""
    document_id: str
    file_key: str
    file_name: str
    file_size: int
    mime_type: str
    created_by_id: str
    changes_description: Optional[str] = None 