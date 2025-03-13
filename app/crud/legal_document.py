from typing import List, Optional, Dict, Any, Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, or_, and_, String, desc
from sqlalchemy.sql import func
import logging
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from uuid import UUID

from app.db.models.legal_document import (
    LegalDocument, LegalDocumentVersion, LegalDocumentArticle,
    LegalDocumentRelationship, LegalDocumentCitation,
    LegalDocumentAnnotation, LegalDocumentArticleAmendment
)
from app.schemas.legal_document import (
    LegalDocumentCreate, LegalDocumentUpdate,
    LegalDocumentVersionCreate, LegalDocumentArticleCreate,
    LegalDocumentRelationshipCreate, LegalDocumentCitationCreate,
    LegalDocumentAnnotationCreate, LegalDocumentArticleAmendmentCreate
)

logger = logging.getLogger(__name__)


async def get_legal_document(
    db: AsyncSession,
    document_id: UUID,
    include_versions: bool = False,
    include_articles: bool = False,
    include_annotations: bool = False
) -> Optional[LegalDocument]:
    """
    Get a legal document by ID.
    """
    query = select(LegalDocument).where(LegalDocument.id == document_id)
    
    if include_versions:
        query = query.options(joinedload(LegalDocument.versions))
    if include_articles:
        query = query.options(joinedload(LegalDocument.articles))
    if include_annotations:
        query = query.options(joinedload(LegalDocument.annotations))
    
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_legal_documents(
    db: AsyncSession, 
    skip: int = 0, 
    limit: int = 100,
    document_type: Optional[str] = None,
    is_abolished: Optional[bool] = None,
    is_updated: Optional[bool] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None
) -> List[LegalDocument]:
    """
    Get multiple legal documents with optional filtering.
    """
    query = (
        select(LegalDocument)
        .options(joinedload(LegalDocument.versions))
        .order_by(LegalDocument.created_at.desc())
    )
    
    if document_type:
        query = query.where(LegalDocument.document_type == document_type)
    
    if is_abolished is not None:
        query = query.where(LegalDocument.is_abolished == is_abolished)
        
    if is_updated is not None:
        query = query.where(LegalDocument.is_updated == is_updated)
        
    if user_id is not None:
        query = query.where(LegalDocument.user_id == user_id)
        
    if status is not None:
        query = query.where(LegalDocument.status == status)
        
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def create_legal_document(
    db: AsyncSession,
    document: LegalDocumentCreate
) -> LegalDocument:
    """
    Create a new legal document.
    """
    try:
        db_document = LegalDocument(**document.model_dump(exclude_unset=True))
        db.add(db_document)
        await db.commit()
        await db.refresh(db_document)
        return db_document
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating legal document: {str(e)}")
        raise


async def update_legal_document(
    db: AsyncSession,
    document_id: UUID,
    document_update: LegalDocumentUpdate
) -> Optional[LegalDocument]:
    """
    Update a legal document.
    """
    query = select(LegalDocument).where(LegalDocument.id == document_id)
    result = await db.execute(query)
    document = result.scalar_one_or_none()
    
    if document:
        update_data = document_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(document, key, value)
        await db.commit()
        await db.refresh(document)
    
    return document


async def delete_legal_document(
    db: AsyncSession,
    document_id: UUID
) -> bool:
    """
    Delete a legal document.
    """
    query = select(LegalDocument).where(LegalDocument.id == document_id)
    result = await db.execute(query)
    document = result.scalar_one_or_none()
    
    if document:
        await db.delete(document)
        await db.commit()
        return True
    return False


async def batch_create_legal_documents(
    db: AsyncSession, documents: List[LegalDocumentCreate]
) -> List[LegalDocument]:
    """
    Create multiple legal documents in a batch.
    """
    db_documents = [LegalDocument(**doc.model_dump()) for doc in documents]
    db.add_all(db_documents)
    await db.commit()
    
    for doc in db_documents:
        await db.refresh(doc)
        
    return db_documents


async def mark_document_as_abolished(
    db: AsyncSession, document_id: str
) -> Optional[LegalDocument]:
    """
    Mark a legal document as abolished.
    """
    return await update_legal_document(
        db, document_id, {"is_abolished": True}
    )


async def mark_document_as_updated(
    db: AsyncSession, document_id: str
) -> Optional[LegalDocument]:
    """
    Mark a legal document as updated.
    """
    return await update_legal_document(
        db, document_id, {"is_updated": True}
    )


async def create_legal_document_version(
    db: AsyncSession,
    version: LegalDocumentVersionCreate
) -> LegalDocumentVersion:
    """Create a new version of a legal document."""
    # Get the current highest version number
    query = select(LegalDocumentVersion).where(
        LegalDocumentVersion.document_id == version.document_id
    ).order_by(desc(LegalDocumentVersion.version_number))
    result = await db.execute(query)
    latest_version = result.scalar_one_or_none()
    
    # Create new version with incremented version number
    new_version_number = (latest_version.version_number + 1) if latest_version else 1
    db_version = LegalDocumentVersion(
        **version.model_dump(exclude_unset=True),
        version_number=new_version_number
    )
    
    db.add(db_version)
    await db.commit()
    await db.refresh(db_version)
    return db_version


async def get_legal_document_versions(
    db: AsyncSession,
    document_id: str,
    skip: int = 0,
    limit: int = 100
) -> List[LegalDocumentVersion]:
    """Get all versions of a legal document."""
    query = (
        select(LegalDocumentVersion)
        .where(LegalDocumentVersion.document_id == document_id)
        .order_by(LegalDocumentVersion.version_number.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    return result.scalars().all()


async def get_legal_document_version(
    db: AsyncSession,
    document_id: str,
    version_number: int
) -> Optional[LegalDocumentVersion]:
    """Get a specific version of a legal document."""
    result = await db.execute(
        select(LegalDocumentVersion).where(
            and_(
                LegalDocumentVersion.document_id == document_id,
                LegalDocumentVersion.version_number == version_number
            )
        )
    )
    return result.scalar_one_or_none()


async def search_legal_documents(
    db: AsyncSession,
    query: str,
    document_type: Optional[str] = None,
    include_abolished: bool = False,
    limit: int = 10,
    offset: int = 0
) -> List[LegalDocument]:
    """Search legal documents by title and metadata."""
    conditions = []
    
    if document_type:
        conditions.append(LegalDocument.document_type == document_type)
    if not include_abolished:
        conditions.append(LegalDocument.is_abolished == False)
    
    # Full-text search condition using to_tsvector
    search_condition = """
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))
        @@ plainto_tsquery('english', :query)
    """
    conditions.append(search_condition)
    
    query = select(LegalDocument).where(
        and_(*conditions)
    ).limit(limit).offset(offset)
    
    result = await db.execute(query, {"query": query})
    return list(result.scalars().all())


async def get_related_documents(
    db: AsyncSession,
    document_id: UUID,
    relationship_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    # Get direct relationships
    conditions = [
        or_(
            LegalDocumentRelationship.source_document_id == document_id,
            LegalDocumentRelationship.target_document_id == document_id
        )
    ]
    if relationship_types:
        conditions.append(LegalDocumentRelationship.relationship_type.in_(relationship_types))
    
    query = select(LegalDocumentRelationship).where(and_(*conditions))
    result = await db.execute(query)
    relationships = list(result.scalars().all())
    
    # Get related documents
    related_docs = []
    for rel in relationships:
        related_id = rel.target_document_id if rel.source_document_id == document_id else rel.source_document_id
        doc = await get_legal_document(db, related_id)
        if doc:
            related_docs.append({
                "document": doc,
                "relationship_type": rel.relationship_type,
                "relationship_metadata": rel.metadata
            })
    
    return related_docs


async def create_legal_document_article(
    db: AsyncSession,
    article: LegalDocumentArticleCreate
) -> LegalDocumentArticle:
    db_article = LegalDocumentArticle(**article.model_dump(exclude_unset=True))
    db.add(db_article)
    await db.commit()
    await db.refresh(db_article)
    return db_article


async def get_document_articles(
    db: AsyncSession,
    document_id: UUID
) -> List[LegalDocumentArticle]:
    query = select(LegalDocumentArticle).where(
        LegalDocumentArticle.document_id == document_id
    ).order_by(LegalDocumentArticle.article_number)
    result = await db.execute(query)
    return list(result.scalars().all())


async def create_legal_document_relationship(
    db: AsyncSession,
    relationship: LegalDocumentRelationshipCreate
) -> LegalDocumentRelationship:
    db_relationship = LegalDocumentRelationship(**relationship.model_dump(exclude_unset=True))
    db.add(db_relationship)
    await db.commit()
    await db.refresh(db_relationship)
    return db_relationship


async def get_document_relationships(
    db: AsyncSession,
    document_id: UUID,
    relationship_type: Optional[str] = None
) -> List[LegalDocumentRelationship]:
    conditions = [
        or_(
            LegalDocumentRelationship.source_document_id == document_id,
            LegalDocumentRelationship.target_document_id == document_id
        )
    ]
    if relationship_type:
        conditions.append(LegalDocumentRelationship.relationship_type == relationship_type)
    
    query = select(LegalDocumentRelationship).where(and_(*conditions))
    result = await db.execute(query)
    return list(result.scalars().all())


async def create_legal_document_citation(
    db: AsyncSession,
    citation: LegalDocumentCitationCreate
) -> LegalDocumentCitation:
    db_citation = LegalDocumentCitation(**citation.model_dump(exclude_unset=True))
    db.add(db_citation)
    await db.commit()
    await db.refresh(db_citation)
    return db_citation


async def get_document_citations(
    db: AsyncSession,
    document_id: UUID,
    as_source: bool = True
) -> List[LegalDocumentCitation]:
    query = select(LegalDocumentCitation).where(
        LegalDocumentCitation.source_document_id == document_id if as_source
        else LegalDocumentCitation.cited_document_id == document_id
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def create_legal_document_annotation(
    db: AsyncSession,
    annotation: LegalDocumentAnnotationCreate
) -> LegalDocumentAnnotation:
    db_annotation = LegalDocumentAnnotation(**annotation.model_dump(exclude_unset=True))
    db.add(db_annotation)
    await db.commit()
    await db.refresh(db_annotation)
    return db_annotation


async def get_document_annotations(
    db: AsyncSession,
    document_id: UUID,
    article_id: Optional[UUID] = None
) -> List[LegalDocumentAnnotation]:
    conditions = [LegalDocumentAnnotation.document_id == document_id]
    if article_id:
        conditions.append(LegalDocumentAnnotation.article_id == article_id)
    
    query = select(LegalDocumentAnnotation).where(and_(*conditions))
    result = await db.execute(query)
    return list(result.scalars().all())


async def create_article_amendment(
    db: AsyncSession,
    amendment: LegalDocumentArticleAmendmentCreate
) -> LegalDocumentArticleAmendment:
    db_amendment = LegalDocumentArticleAmendment(**amendment.model_dump(exclude_unset=True))
    db.add(db_amendment)
    await db.commit()
    await db.refresh(db_amendment)
    return db_amendment


async def get_article_amendments(
    db: AsyncSession,
    article_id: UUID
) -> List[LegalDocumentArticleAmendment]:
    query = select(LegalDocumentArticleAmendment).where(
        LegalDocumentArticleAmendment.article_id == article_id
    ).order_by(desc(LegalDocumentArticleAmendment.amendment_date))
    result = await db.execute(query)
    return list(result.scalars().all()) 