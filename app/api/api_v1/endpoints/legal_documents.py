from typing import List, Optional
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Body, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
import os
import aiofiles
from datetime import datetime

from app.core.auth import get_current_active_user
from app.core.database import get_db
from app.crud import legal_document as crud
from app.core.celery import process_document_task
from app.core.config import settings
from app.schemas.legal_document import (
    LegalDocument, LegalDocumentCreate, LegalDocumentUpdate,
    LegalDocumentVersion, LegalDocumentVersionCreate,
    LegalDocumentArticle, LegalDocumentArticleCreate,
    LegalDocumentRelationship, LegalDocumentRelationshipCreate,
    LegalDocumentCitation, LegalDocumentCitationCreate,
    LegalDocumentAnnotation, LegalDocumentAnnotationCreate,
    LegalDocumentArticleAmendment, LegalDocumentArticleAmendmentCreate,
    DocumentType, DocumentStatus, LegalDocumentResponse
)
from app.core.document_processor import DocumentProcessor

router = APIRouter()

# Document endpoints
@router.post("/", response_model=LegalDocument)
async def create_legal_document(
    *,
    db: AsyncSession = Depends(get_db),
    document_in: LegalDocumentCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocument:
    """Create a new legal document."""
    document_in.created_by_id = current_user.id
    return await crud.create_legal_document(db, document_in)

@router.get("/{document_id}", response_model=LegalDocument)
async def get_legal_document(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    include_versions: bool = Query(False, description="Include document versions"),
    include_articles: bool = Query(False, description="Include document articles"),
    include_annotations: bool = Query(False, description="Include document annotations"),
    _: dict = Depends(get_current_active_user)
) -> LegalDocument:
    """Get a legal document by ID."""
    document = await crud.get_legal_document(
        db, document_id,
        include_versions=include_versions,
        include_articles=include_articles,
        include_annotations=include_annotations
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document

@router.put("/{document_id}", response_model=LegalDocument)
async def update_legal_document(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    document_in: LegalDocumentUpdate,
    current_user = Depends(get_current_active_user)
) -> LegalDocument:
    """Update a legal document."""
    document = await crud.get_legal_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    document_in.updated_by_id = current_user.id
    return await crud.update_legal_document(db, document_id, document_in)

@router.delete("/{document_id}")
async def delete_legal_document(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    _: dict = Depends(get_current_active_user)
) -> dict:
    """Delete a legal document."""
    if await crud.delete_legal_document(db, document_id):
        return {"message": "Document deleted successfully"}
    raise HTTPException(status_code=404, detail="Document not found")

# Version endpoints
@router.post("/{document_id}/versions", response_model=LegalDocumentVersion)
async def create_document_version(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    version_in: LegalDocumentVersionCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocumentVersion:
    """Create a new version of a legal document."""
    document = await crud.get_legal_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    version_in.document_id = document_id
    version_in.created_by_id = current_user.id
    return await crud.create_legal_document_version(db, version_in)

# Article endpoints
@router.post("/{document_id}/articles", response_model=LegalDocumentArticle)
async def create_document_article(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    article_in: LegalDocumentArticleCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocumentArticle:
    """Create a new article in a legal document."""
    document = await crud.get_legal_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    article_in.document_id = document_id
    article_in.created_by_id = current_user.id
    return await crud.create_legal_document_article(db, article_in)

@router.get("/{document_id}/articles", response_model=List[LegalDocumentArticle])
async def get_document_articles(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    _: dict = Depends(get_current_active_user)
) -> List[LegalDocumentArticle]:
    """Get all articles in a legal document."""
    document = await crud.get_legal_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return await crud.get_document_articles(db, document_id)

# Relationship endpoints
@router.post("/relationships", response_model=LegalDocumentRelationship)
async def create_document_relationship(
    *,
    db: AsyncSession = Depends(get_db),
    relationship_in: LegalDocumentRelationshipCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocumentRelationship:
    """Create a relationship between legal documents."""
    relationship_in.created_by_id = current_user.id
    return await crud.create_legal_document_relationship(db, relationship_in)

@router.get("/{document_id}/relationships", response_model=List[LegalDocumentRelationship])
async def get_document_relationships(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    relationship_type: Optional[str] = None,
    _: dict = Depends(get_current_active_user)
) -> List[LegalDocumentRelationship]:
    """Get all relationships for a legal document."""
    return await crud.get_document_relationships(db, document_id, relationship_type)

# Citation endpoints
@router.post("/citations", response_model=LegalDocumentCitation)
async def create_document_citation(
    *,
    db: AsyncSession = Depends(get_db),
    citation_in: LegalDocumentCitationCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocumentCitation:
    """Create a citation between legal documents."""
    citation_in.created_by_id = current_user.id
    return await crud.create_legal_document_citation(db, citation_in)

@router.get("/{document_id}/citations", response_model=List[LegalDocumentCitation])
async def get_document_citations(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    as_source: bool = Query(True, description="Get citations where this document is the source"),
    _: dict = Depends(get_current_active_user)
) -> List[LegalDocumentCitation]:
    """Get all citations for a legal document."""
    return await crud.get_document_citations(db, document_id, as_source)

# Annotation endpoints
@router.post("/annotations", response_model=LegalDocumentAnnotation)
async def create_document_annotation(
    *,
    db: AsyncSession = Depends(get_db),
    annotation_in: LegalDocumentAnnotationCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocumentAnnotation:
    """Create an annotation for a legal document or article."""
    annotation_in.created_by_id = current_user.id
    return await crud.create_legal_document_annotation(db, annotation_in)

@router.get("/{document_id}/annotations", response_model=List[LegalDocumentAnnotation])
async def get_document_annotations(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    article_id: Optional[UUID] = None,
    _: dict = Depends(get_current_active_user)
) -> List[LegalDocumentAnnotation]:
    """Get all annotations for a legal document or article."""
    return await crud.get_document_annotations(db, document_id, article_id)

# Amendment endpoints
@router.post("/articles/{article_id}/amendments", response_model=LegalDocumentArticleAmendment)
async def create_article_amendment(
    *,
    db: AsyncSession = Depends(get_db),
    article_id: UUID,
    amendment_in: LegalDocumentArticleAmendmentCreate,
    current_user = Depends(get_current_active_user)
) -> LegalDocumentArticleAmendment:
    """Create an amendment for a legal document article."""
    amendment_in.article_id = article_id
    amendment_in.created_by_id = current_user.id
    return await crud.create_article_amendment(db, amendment_in)

@router.get("/articles/{article_id}/amendments", response_model=List[LegalDocumentArticleAmendment])
async def get_article_amendments(
    *,
    db: AsyncSession = Depends(get_db),
    article_id: UUID,
    _: dict = Depends(get_current_active_user)
) -> List[LegalDocumentArticleAmendment]:
    """Get all amendments for a legal document article."""
    return await crud.get_article_amendments(db, article_id)

# Search endpoints
@router.get("/search/", response_model=List[LegalDocument])
async def search_legal_documents(
    *,
    db: AsyncSession = Depends(get_db),
    query: str = Query(..., description="Search query string"),
    document_type: Optional[str] = Query(None, description="Filter by document type"),
    include_abolished: bool = Query(False, description="Include abolished documents"),
    limit: int = Query(10, description="Maximum number of results"),
    offset: int = Query(0, description="Number of results to skip"),
    _: dict = Depends(get_current_active_user)
) -> List[LegalDocument]:
    """Search legal documents by content and metadata."""
    return await crud.search_legal_documents(
        db, query, document_type,
        include_abolished=include_abolished,
        limit=limit, offset=offset
    )

@router.get("/{document_id}/related", response_model=List[dict])
async def get_related_documents(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    relationship_types: Optional[List[str]] = Query(None, description="Filter by relationship types"),
    _: dict = Depends(get_current_active_user)
) -> List[dict]:
    """Get all documents related to a legal document."""
    return await crud.get_related_documents(db, document_id, relationship_types)

@router.post("/upload", response_model=LegalDocumentResponse)
async def upload_legal_document(
    *,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    title: Optional[str] = Form(None),
    metadata: Optional[dict] = Form(None),
    current_user = Depends(get_current_active_user)
) -> LegalDocumentResponse:
    """
    Upload and process a legal document immediately.
    
    The document will be:
    1. Validated for size and type
    2. Processed for text extraction and parsing
    3. Saved to S3
    4. Created in the database with processed content
    """
    try:
        # Validate file type
        if file.content_type not in settings.ALLOWED_UPLOAD_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"File type {file.content_type} not allowed. Allowed types: {', '.join(settings.ALLOWED_UPLOAD_TYPES)}"
            )
        
        # Read file content for size validation
        content = await file.read()
        if len(content) > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE / (1024 * 1024)}MB"
            )
        
        # Create temporary file
        temp_file_path = os.path.join(settings.UPLOAD_DIR, f"{uuid4()}_{file.filename}")
        os.makedirs(os.path.dirname(temp_file_path), exist_ok=True)
        
        try:
            # Save uploaded file temporarily
            async with aiofiles.open(temp_file_path, 'wb') as f:
                await f.write(content)
            
            # Initialize document processor
            processor = DocumentProcessor(db)
            
            # Process the file immediately
            with open(temp_file_path, 'rb') as file_obj:
                result = await processor.process_file(
                    file=file_obj,
                    original_filename=file.filename,
                    document_type=document_type.value,
                    user_id=str(current_user.id),
                    title=title,
                    document_metadata=metadata or {}
                )
            
            if result["status"] != "success":
                raise HTTPException(
                    status_code=500,
                    detail=result["message"]
                )
            
            return LegalDocumentResponse(
                id=str(result["document_id"]),
                title=title or file.filename,
                document_type=document_type,
                document_metadata=metadata or {},
                created_at=datetime.now(),
                updated_at=datetime.now(),
                message="Document processed successfully",
                download_url=result["download_url"]
            )
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                
    except Exception as e:
        if not isinstance(e, HTTPException):
            raise HTTPException(
                status_code=500,
                detail=f"Error processing document: {str(e)}"
            )
        raise e

@router.get("/status/{document_id}", response_model=dict)
async def get_legal_document_status(
    *,
    db: AsyncSession = Depends(get_db),
    document_id: UUID,
    _: dict = Depends(get_current_active_user)
) -> dict:
    """Get the processing status of a legal document."""
    document = await crud.get_legal_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    return {
        "status": document.status,
        "title": document.title,
        "document_type": document.document_type,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "metadata": document.document_metadata
    } 