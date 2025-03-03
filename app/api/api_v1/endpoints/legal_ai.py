from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.core.database import get_db
from app.core.auth import get_current_active_user
from app.db.models import User, LegalDocument
from app.schemas.legal_document import (
    LegalDocumentCreate, LegalDocumentResponse, LegalDocumentSearchQuery,
    LegalDocumentSearchResult, LegalDocumentBatchCreate
)
from app.crud import legal_document as crud
from app.ai.retrieval.langchain_service import langchain_service
from app.ai.retrieval.document_scraper import document_scraper

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/documents", response_model=LegalDocumentResponse)
async def create_legal_document(
    document: LegalDocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new legal document and index it for AI retrieval.
    """
    # Create document in database
    db_document = await crud.create_legal_document(db, document)
    
    # Index document in vector store
    vector_ids = await langchain_service.index_documents(
        texts=[db_document.content],
        metadatas=[{
            "id": db_document.id,
            "title": db_document.title,
            "document_type": db_document.document_type,
            "document_metadata": db_document.document_metadata or {}
        }]
    )
    
    # Update document with vector ID
    if vector_ids:
        await crud.update_legal_document(
            db, db_document.id, {"vector_id": vector_ids[0]}
        )
        db_document = await crud.get_legal_document(db, db_document.id)
    
    return db_document


@router.post("/documents/batch", response_model=List[LegalDocumentResponse])
async def batch_create_legal_documents(
    batch: LegalDocumentBatchCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create multiple legal documents in a batch and index them for AI retrieval.
    """
    # Create documents in database
    db_documents = await crud.batch_create_legal_documents(db, batch.documents)
    
    # Index documents in vector store (in background)
    background_tasks.add_task(
        _index_documents_background,
        db_documents=db_documents,
        db=db
    )
    
    return db_documents


async def _index_documents_background(db_documents: List[LegalDocument], db: AsyncSession):
    """Background task to index documents in the vector store."""
    try:
        texts = [doc.content for doc in db_documents]
        metadatas = [{
            "id": doc.id,
            "title": doc.title,
            "document_type": doc.document_type,
            "document_metadata": doc.document_metadata or {}
        } for doc in db_documents]
        
        vector_ids = await langchain_service.index_documents(texts, metadatas)
        
        # Update documents with vector IDs
        for i, doc in enumerate(db_documents):
            if i < len(vector_ids):
                await crud.update_legal_document(
                    db, doc.id, {"vector_id": vector_ids[i]}
                )
    except Exception as e:
        logger.error(f"Error indexing documents: {str(e)}")


@router.post("/documents/upload", response_model=List[LegalDocumentResponse])
async def upload_legal_documents(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a JSON file containing legal documents and index them for AI retrieval.
    """
    # Save uploaded file
    file_path = f"uploads/legal_documents/{file.filename}"
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # Load documents from file
    documents = await document_scraper.load_documents_from_json(file_path)
    
    # Create document objects
    document_creates = []
    for doc in documents:
        document_creates.append(LegalDocumentCreate(
            title=doc.get("title", "Untitled"),
            content=doc.get("content", ""),
            document_type=document_type,
            document_metadata=doc
        ))
    
    # Create documents in database
    db_documents = await crud.batch_create_legal_documents(db, document_creates)
    
    # Index documents in vector store (in background)
    background_tasks.add_task(
        _index_documents_background,
        db_documents=db_documents,
        db=db
    )
    
    return db_documents


@router.post("/search", response_model=List[LegalDocumentSearchResult])
async def search_legal_documents(
    query: LegalDocumentSearchQuery,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Search for legal documents using AI retrieval.
    """
    # Create filter if document type is specified
    filter_dict = None
    if query.document_type:
        filter_dict = {"document_type": query.document_type}
    
    # Retrieve similar documents
    results = await langchain_service.retrieve_similar_documents(
        query=query.query,
        filter=filter_dict,
        top_k=query.limit
    )
    
    # Format results
    search_results = []
    for result in results:
        # Get document from database
        doc_id = result["metadata"].get("id")
        if doc_id:
            db_document = await crud.get_legal_document(db, doc_id)
            if db_document:
                search_results.append({
                    "document": db_document,
                    "score": result["score"]
                })
    
    return search_results


@router.post("/ask", response_model=Dict[str, Any])
async def ask_legal_question(
    query: str = Query(..., description="The legal question to answer"),
    document_type: Optional[str] = Query(None, description="Filter by document type"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Ask a legal question and get an answer based on the indexed legal documents.
    """
    # Create filter if document type is specified
    filter_dict = None
    if document_type:
        filter_dict = {"document_type": document_type}
    
    # Answer the question
    result = await langchain_service.answer_question(
        question=query,
        filter=filter_dict
    )
    
    return result


@router.post("/scrape", response_model=Dict[str, Any])
async def scrape_legal_documents(
    background_tasks: BackgroundTasks,
    document_type: Optional[str] = Query(None, description="Type of documents to scrape"),
    from_date: Optional[str] = Query(None, description="Date to scrape documents from (ISO format)"),
    limit: int = Query(100, description="Maximum number of documents to scrape"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Scrape legal documents from external sources and index them for AI retrieval.
    """
    # Add scraping task to background
    background_tasks.add_task(
        _scrape_documents_background,
        document_type=document_type,
        from_date=from_date,
        limit=limit,
        db=db
    )
    
    return {"message": "Document scraping started in the background"}


async def _scrape_documents_background(
    document_type: Optional[str],
    from_date: Optional[str],
    limit: int,
    db: AsyncSession
):
    """Background task to scrape and index legal documents."""
    try:
        # Fetch documents
        documents = await document_scraper.fetch_documents(
            document_type=document_type,
            from_date=from_date,
            limit=limit
        )
        
        # Save documents to JSON
        file_path = await document_scraper.save_documents_to_json(documents)
        
        # Analyze document relationships
        relationships = await document_scraper.analyze_document_relationships(documents)
        
        # Process abolished documents
        for abolished in relationships["abolished"]:
            await crud.mark_document_as_abolished(db, abolished["id"])
        
        # Process updated documents
        for updated in relationships["updated"]:
            await crud.mark_document_as_updated(db, updated["id"], updated["updated_by"])
        
        # Create document objects for new documents
        document_creates = []
        for doc in documents:
            # Skip if document is already in the database
            existing_doc = await crud.get_legal_document(db, doc["id"])
            if existing_doc:
                continue
                
            document_creates.append(LegalDocumentCreate(
                title=doc.get("title", "Untitled"),
                content=doc.get("content", ""),
                document_type=doc.get("type", document_type or "unknown"),
                document_metadata=doc
            ))
        
        # Create documents in database
        if document_creates:
            db_documents = await crud.batch_create_legal_documents(db, document_creates)
            
            # Index documents in vector store
            texts = [doc.content for doc in db_documents]
            metadatas = [{
                "id": doc.id,
                "title": doc.title,
                "document_type": doc.document_type,
                "document_metadata": doc.document_metadata or {}
            } for doc in db_documents]
            
            vector_ids = await langchain_service.index_documents(texts, metadatas)
            
            # Update documents with vector IDs
            for i, doc in enumerate(db_documents):
                if i < len(vector_ids):
                    await crud.update_legal_document(
                        db, doc.id, {"vector_id": vector_ids[i]}
                    )
    except Exception as e:
        logger.error(f"Error scraping documents: {str(e)}") 