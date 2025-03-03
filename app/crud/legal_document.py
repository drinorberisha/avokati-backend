from typing import List, Optional, Dict, Any, Union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.sql import func
import logging

from app.db.models.legal_document import LegalDocument
from app.schemas.legal_document import LegalDocumentCreate, LegalDocumentUpdate

logger = logging.getLogger(__name__)


async def get_legal_document(db: AsyncSession, document_id: str) -> Optional[LegalDocument]:
    """
    Get a legal document by ID.
    """
    result = await db.execute(select(LegalDocument).where(LegalDocument.id == document_id))
    return result.scalars().first()


async def get_legal_documents(
    db: AsyncSession, 
    skip: int = 0, 
    limit: int = 100,
    document_type: Optional[str] = None,
    is_abolished: Optional[bool] = None,
    is_updated: Optional[bool] = None
) -> List[LegalDocument]:
    """
    Get multiple legal documents with optional filtering.
    """
    query = select(LegalDocument)
    
    if document_type:
        query = query.where(LegalDocument.document_type == document_type)
    
    if is_abolished is not None:
        query = query.where(LegalDocument.is_abolished == is_abolished)
        
    if is_updated is not None:
        query = query.where(LegalDocument.is_updated == is_updated)
        
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def create_legal_document(
    db: AsyncSession, document: LegalDocumentCreate
) -> LegalDocument:
    """
    Create a new legal document.
    """
    db_document = LegalDocument(**document.model_dump())
    db.add(db_document)
    await db.commit()
    await db.refresh(db_document)
    return db_document


async def update_legal_document(
    db: AsyncSession, document_id: str, document: Union[LegalDocumentUpdate, Dict[str, Any]]
) -> Optional[LegalDocument]:
    """
    Update a legal document.
    """
    if isinstance(document, LegalDocumentUpdate):
        update_data = document.model_dump(exclude_unset=True)
    else:
        update_data = document
        
    if not update_data:
        return await get_legal_document(db, document_id)
        
    stmt = (
        update(LegalDocument)
        .where(LegalDocument.id == document_id)
        .values(**update_data)
        .returning(LegalDocument)
    )
    
    result = await db.execute(stmt)
    await db.commit()
    return result.scalars().first()


async def delete_legal_document(db: AsyncSession, document_id: str) -> bool:
    """
    Delete a legal document.
    """
    stmt = delete(LegalDocument).where(LegalDocument.id == document_id)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount > 0


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
    db: AsyncSession, document_id: str, new_document_id: str
) -> Optional[LegalDocument]:
    """
    Mark a legal document as updated and link it to the new version.
    """
    return await update_legal_document(
        db, document_id, {"is_updated": True, "parent_document_id": new_document_id}
    ) 