from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import os
import uuid
import tempfile
import shutil
from datetime import datetime

from app.core.database import get_db
from app.core.auth import get_current_active_user
from app.db.models import User
from app.db.models.legal_document import LegalDocument
from app.schemas.legal_document import (
    LegalDocumentCreate, LegalDocumentResponse, LegalDocumentSearchQuery,
    LegalDocumentSearchResult, LegalDocumentBatchCreate, LegalDocumentList
)
from app.crud import legal_document as crud
from app.ai.retrieval.langchain_service import langchain_service
from app.ai.retrieval.document_scraper import document_scraper
from app.services.legal_document_service import LegalDocumentService
from app.ai.retrieval.vector_store import VectorStoreClient, vector_store_client

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize services
legal_document_service = LegalDocumentService()

# Define supported file types
SUPPORTED_FILE_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "txt": "text/plain",
    "rtf": "application/rtf",
    "html": "text/html",
    "htm": "text/html",
    "json": "application/json",
}

def get_file_extension(filename: str) -> str:
    """Get the file extension from a filename."""
    return filename.split(".")[-1].lower()

async def process_document_task(
    file_path: str, 
    document_type: str, 
    original_filename: str,
    user_id: str,
    document_id: uuid.UUID
):
    """Background task to process a document."""
    try:
        # Import here to avoid circular imports
        from app.scripts.document_processor import DocumentProcessor
        
        processor = DocumentProcessor()
        
        # Open the file and process it
        with open(file_path, "rb") as file:
            # Process the document
            result = await processor.process_file(
                file=file,
                original_filename=original_filename,
                document_type=document_type,
                user_id=user_id
            )
            
            if result["status"] == "success":
                # Update document status in database
                await legal_document_service.update_document_status(document_id, "processed")
                logger.info(f"Successfully processed legal document {original_filename} for user {user_id}")
            else:
                # Update document status to failed
                await legal_document_service.update_document_status(document_id, "failed")
                logger.error(f"Error processing document content: {result['message']}")
        
        # Clean up the temporary file
        if os.path.exists(file_path):
            os.remove(file_path)
            
    except Exception as e:
        # Update document status to failed
        await legal_document_service.update_document_status(document_id, "failed")
        
        # Log the error
        logger.error(f"Error processing legal document {original_filename}: {str(e)}")
        
        # Clean up the temporary file
        if os.path.exists(file_path):
            os.remove(file_path)

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


@router.post("/file-upload", response_model=LegalDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_legal_document_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form("other"),
    title: Optional[str] = Form(None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload a legal document file (PDF, DOCX, etc.) for processing and indexing.
    
    The document will be processed in the background and indexed in the vector store.
    """
    # Validate file type
    file_ext = get_file_extension(file.filename)
    if file_ext not in SUPPORTED_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file_ext}. Supported types: {', '.join(SUPPORTED_FILE_TYPES.keys())}"
        )
    
    # Validate document type
    valid_document_types = ["law", "regulation", "case_law", "contract", "article", "other"]
    if document_type not in valid_document_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document type. Valid types: {', '.join(valid_document_types)}"
        )
    
    # Create a unique document ID
    document_id = uuid.uuid4()
    
    # Use the provided title or the filename
    document_title = title or os.path.splitext(file.filename)[0]
    
    # Save the file to a temporary location
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"{document_id}_{file.filename}")
    
    try:
        # Convert user_id to string to avoid Pydantic validation error
        user_id_str = str(current_user.id)
        
        # Create the document in the database with "pending" status
        document = LegalDocumentCreate(
            id=document_id,
            title=document_title,
            document_type=document_type,
            status="pending",
            user_id=user_id_str,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            file_path=file_path,
            original_filename=file.filename
        )
        
        # Save the document to the database using the appropriate service
        if hasattr(crud, 'create_legal_document'):
            db_document = await crud.create_legal_document(db, document)
        else:
            db_document = await legal_document_service.create_document(document)
        
        # Save the uploaded file to the temporary location
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process the document in the background
        background_tasks.add_task(
            process_document_task,
            file_path=file_path,
            document_type=document_type,
            original_filename=file.filename,
            user_id=user_id_str,
            document_id=document_id
        )
        
        return LegalDocumentResponse(
            id=db_document.id,
            title=db_document.title,
            document_type=db_document.document_type,
            status="pending",
            message="Legal document uploaded and queued for processing",
            created_at=db_document.created_at,
            updated_at=db_document.updated_at
        )
        
    except Exception as e:
        # Clean up the temporary file if it exists
        if os.path.exists(file_path):
            os.remove(file_path)
        
        logger.error(f"Error uploading legal document: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading legal document: {str(e)}"
        )


@router.get("/documents", response_model=List[LegalDocumentList])
async def get_legal_documents(
    skip: int = 0,
    limit: int = 100,
    document_type: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all legal documents for the current user."""
    if hasattr(crud, 'get_legal_documents'):
        documents = await crud.get_legal_documents(
            db,
            user_id=current_user.id,
            skip=skip,
            limit=limit,
            document_type=document_type,
            status=status
        )
    else:
        documents = await legal_document_service.get_documents(
            user_id=current_user.id,
            skip=skip,
            limit=limit,
            document_type=document_type,
            status=status
        )
    return documents


@router.get("/documents/{document_id}", response_model=LegalDocumentResponse)
async def get_legal_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get a legal document by ID."""
    if hasattr(crud, 'get_legal_document'):
        document = await crud.get_legal_document(db, document_id)
    else:
        document = await legal_document_service.get_document(document_id, current_user.id)
        
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Legal document with ID {document_id} not found"
        )
    return document


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_legal_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a legal document by ID."""
    # Check if document exists
    if hasattr(crud, 'get_legal_document'):
        document = await crud.get_legal_document(db, document_id)
    else:
        document = await legal_document_service.get_document(document_id, current_user.id)
        
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Legal document with ID {document_id} not found"
        )
    
    # Delete from vector store
    try:
        if hasattr(langchain_service, 'delete_document'):
            await langchain_service.delete_document(document_id)
        else:
            await vector_store_client.delete([document_id])
    except Exception as e:
        logger.error(f"Error deleting legal document from vector store: {str(e)}")
    
    # Delete from database
    if hasattr(crud, 'delete_legal_document'):
        await crud.delete_legal_document(db, document_id)
    else:
        await legal_document_service.delete_document(document_id, current_user.id)
    
    return None


@router.delete("/documents", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_legal_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete all legal documents for the current user."""
    # Get all document IDs for the user
    if hasattr(crud, 'get_legal_documents'):
        documents = await crud.get_legal_documents(db, user_id=current_user.id)
    else:
        documents = await legal_document_service.get_documents(user_id=current_user.id)
    
    document_ids = [doc.id for doc in documents]
    
    # Delete from vector store
    try:
        for doc_id in document_ids:
            if hasattr(langchain_service, 'delete_document'):
                await langchain_service.delete_document(doc_id)
            else:
                await vector_store_client.delete([doc_id])
    except Exception as e:
        logger.error(f"Error deleting legal documents from vector store: {str(e)}")
    
    # Delete from database
    if hasattr(crud, 'delete_all_legal_documents'):
        await crud.delete_all_legal_documents(db, current_user.id)
    else:
        await legal_document_service.delete_all_documents(current_user.id)
    
    return None


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
            if hasattr(crud, 'get_legal_document'):
                db_document = await crud.get_legal_document(db, doc_id)
            else:
                db_document = await legal_document_service.get_document(doc_id, current_user.id)
                
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
            # Logic for handling abolished documents
            pass
            
        # Process amended documents
        for amended in relationships["amended"]:
            # Logic for handling amended documents
            pass
            
        # Create document objects
        document_creates = []
        for doc in documents:
            document_creates.append(LegalDocumentCreate(
                title=doc.get("title", "Untitled"),
                content=doc.get("content", ""),
                document_type=document_type or doc.get("type", "other"),
                document_metadata=doc
            ))
        
        # Create documents in database
        db_documents = await crud.batch_create_legal_documents(db, document_creates)
        
        # Index documents in vector store
        await _index_documents_background(db_documents, db)
        
    except Exception as e:
        logger.error(f"Error scraping legal documents: {str(e)}") 