from typing import List, Optional, Dict, Any
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.sql import and_
import uuid

from app.core.database import get_db_context
from app.db.models.legal_document import LegalDocument
from app.schemas.legal_document import LegalDocumentCreate, LegalDocumentUpdate

logger = logging.getLogger(__name__)

class LegalDocumentService:
    """Service for handling legal documents."""
    
    async def create_document(self, document: LegalDocumentCreate) -> LegalDocument:
        """Create a new legal document."""
        async with get_db_context() as session:
            db_document = LegalDocument(
                id=document.id,
                title=document.title,
                document_type=document.document_type,
                status=document.status,
                user_id=document.user_id,
                created_at=document.created_at,
                updated_at=document.updated_at,
                file_path=document.file_path,
                original_filename=document.original_filename,
                document_metadata=document.metadata or {}
            )
            
            session.add(db_document)
            await session.commit()
            await session.refresh(db_document)
            
            logger.info(f"Created legal document: {db_document.id}")
            return db_document
    
    async def get_document(self, document_id: str, user_id: str) -> Optional[LegalDocument]:
        """Get a legal document by ID."""
        async with get_db_context() as session:
            query = select(LegalDocument).where(
                and_(
                    LegalDocument.id == document_id,
                    LegalDocument.user_id == user_id
                )
            )
            result = await session.execute(query)
            document = result.scalars().first()
            
            return document
    
    async def get_documents(
        self, 
        user_id: str, 
        skip: int = 0, 
        limit: int = 100,
        document_type: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[LegalDocument]:
        """Get all legal documents for a user."""
        async with get_db_context() as session:
            query = select(LegalDocument).where(LegalDocument.user_id == user_id)
            
            if document_type:
                query = query.where(LegalDocument.document_type == document_type)
            
            if status:
                query = query.where(LegalDocument.status == status)
            
            query = query.offset(skip).limit(limit)
            result = await session.execute(query)
            documents = result.scalars().all()
            
            return list(documents)
    
    async def update_document(
        self, 
        document_id: str, 
        user_id: str, 
        document_update: LegalDocumentUpdate
    ) -> Optional[LegalDocument]:
        """Update a legal document."""
        async with get_db_context() as session:
            # Check if document exists and belongs to user
            query = select(LegalDocument).where(
                and_(
                    LegalDocument.id == document_id,
                    LegalDocument.user_id == user_id
                )
            )
            result = await session.execute(query)
            document = result.scalars().first()
            
            if not document:
                return None
            
            # Prepare update data
            update_data = document_update.dict(exclude_unset=True)
            
            # Update document
            stmt = update(LegalDocument).where(
                and_(
                    LegalDocument.id == document_id,
                    LegalDocument.user_id == user_id
                )
            ).values(**update_data)
            
            await session.execute(stmt)
            await session.commit()
            
            # Get updated document
            result = await session.execute(query)
            updated_document = result.scalars().first()
            
            logger.info(f"Updated legal document: {document_id}")
            return updated_document
    
    async def update_document_status(self, document_id: uuid.UUID, status: str) -> Optional[LegalDocument]:
        """Update a legal document's status."""
        async with get_db_context() as session:
            # Check if document exists
            query = select(LegalDocument).where(LegalDocument.id == document_id)
            result = await session.execute(query)
            document = result.scalars().first()
            
            if not document:
                return None
            
            # Update document status
            stmt = update(LegalDocument).where(
                LegalDocument.id == document_id
            ).values(
                status=status,
                updated_at=datetime.now()
            )
            
            await session.execute(stmt)
            await session.commit()
            
            # Get updated document
            result = await session.execute(query)
            updated_document = result.scalars().first()
            
            logger.info(f"Updated legal document status: {document_id} -> {status}")
            return updated_document
    
    async def delete_document(self, document_id: str, user_id: str) -> bool:
        """Delete a legal document."""
        async with get_db_context() as session:
            # Check if document exists and belongs to user
            query = select(LegalDocument).where(
                and_(
                    LegalDocument.id == document_id,
                    LegalDocument.user_id == user_id
                )
            )
            result = await session.execute(query)
            document = result.scalars().first()
            
            if not document:
                return False
            
            # Delete document
            stmt = delete(LegalDocument).where(
                and_(
                    LegalDocument.id == document_id,
                    LegalDocument.user_id == user_id
                )
            )
            
            await session.execute(stmt)
            await session.commit()
            
            logger.info(f"Deleted legal document: {document_id}")
            return True
    
    async def delete_all_documents(self, user_id: str) -> bool:
        """Delete all legal documents for a user."""
        async with get_db_context() as session:
            # Delete all documents for user
            stmt = delete(LegalDocument).where(LegalDocument.user_id == user_id)
            
            await session.execute(stmt)
            await session.commit()
            
            logger.info(f"Deleted all legal documents for user: {user_id}")
            return True 