from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import os
import uuid
import tempfile
import shutil
from datetime import datetime
from pydantic import BaseModel

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
    
    # Format results directly from vector store results without database lookup
    search_results = []
    for result in results:
        metadata = result["document_metadata"]
        
        # Create document object directly from vector store metadata
        document = {
            "id": metadata.get("id", "unknown"),
            "title": metadata.get("law_name", metadata.get("title", "Unknown Document")),
            "content": result["content"],
            "document_type": metadata.get("document_type", "other"),
            "document_metadata": metadata,
            # Add default created_at if missing
            "created_at": metadata.get("created_at", datetime.now().isoformat()),
            # Add other required fields with defaults
            "user_id": current_user.id,
            "status": metadata.get("status", "active"),
            "vector_id": metadata.get("id", "unknown"),
            # Add any other required fields
            "updated_at": metadata.get("updated_at", datetime.now().isoformat())
        }
        
        search_results.append({
            "document": document,
            "score": result["score"]
        })
    
    return search_results


class AskQuestionRequest(BaseModel):
    query: str
    document_type: Optional[str] = None
    top_k: Optional[int] = 5

@router.post("/ask", response_model=Dict[str, Any])
async def ask_legal_question(
    request: AskQuestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Ask a legal question and get an answer based on the indexed legal documents.

    LEGACY endpoint (v1 namespace + English prompt + no citation validation).
    For new frontend code, use `/legal-ai/ask-v2` which returns a rich
    response in a single call.
    """
    # Create filter if document type is specified
    filter_dict = None
    if request.document_type:
        filter_dict = {"document_type": request.document_type}

    # Answer the question
    result = await langchain_service.answer_question(
        question=request.query,
        filter=filter_dict,
        top_k=request.top_k
    )

    return result


# ---- /legal-ai/ask-v2: post-rebuild AvokAI endpoint ----------------------
# Single-call rich response: routes the query through pipeline.answer()
# (router → retrieve → generate → validate), enriches each retrieved chunk
# with abolishment status + per-law catalog data (gazette URL, publication
# date), and returns everything the new sidebar UI needs.
#
# Why a new endpoint instead of replacing /ask:
#   - Reversible: legacy /ask still works for any client that hasn't migrated
#   - Different response shape: this one is structured for direct UI render
#   - Different namespace: defaults to default_v2 instead of legacy default
# Once the React app is fully migrated, /ask can be deleted.

from uuid import UUID  # noqa: E402

from app.ai.embedding.providers import EmbeddingUnavailableError  # noqa: E402
from app.ai.pipeline import answer as pipeline_answer  # noqa: E402
from app.ai.v2_adapter import adapt_pipeline_result_to_v2  # noqa: E402
from app.schemas.avokai import AskV2Request, AskV2Response  # noqa: E402
from app.schemas.chat import (  # noqa: E402
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionSummary,
    ChatSessionUpdate,
)
from app.crud import chat as chat_crud  # noqa: E402


@router.post("/ask-v2", response_model=AskV2Response)
async def ask_legal_question_v2(
    request: AskV2Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Single-call rich answer for AvokAI.

    Routes through `pipeline.answer()` and returns the structured shape the
    new frontend sidebar consumes directly. Replaces the historical
    search + ask double-call pattern. Adapter logic lives in
    `app.ai.v2_adapter` so it can be unit-tested without the FastAPI / DB
    stack.

    If `session_id` is provided AND owned by the caller, the user+assistant
    turn is persisted into `chat_messages` after generation. Persistence
    failures don't fail the request — the answer still goes back to the
    user; we just log the storage error.

    Error mapping for the UI:
      - EmbeddingUnavailableError -> 503 with code `EMBEDDING_UNAVAILABLE`,
        so the frontend can show "Shërbimi i kërkimit nuk është i
        disponueshëm momentalisht" instead of a generic toast.
    """
    history = list(request.conversation_history or [])[-6:]
    try:
        result = pipeline_answer(
            request.query,
            namespace=request.namespace,
            use_llm=request.use_llm,
            conversation_history=history if history else None,
        )
    except EmbeddingUnavailableError as e:
        logger.error("ask-v2: embedding provider unavailable: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EMBEDDING_UNAVAILABLE",
                "message": (
                    "Shërbimi i kërkimit nuk është i disponueshëm momentalisht. "
                    "Ju lutemi provoni përsëri për pak minuta."
                ),
            },
        )
    response = adapt_pipeline_result_to_v2(result)

    # Best-effort persistence. The pipeline already ran; if storage fails
    # we still want the user to see their answer.
    if request.session_id:
        try:
            sid = UUID(request.session_id)
            await chat_crud.append_turn(
                db,
                session_id=sid,
                user_id=current_user.id,
                user_content=request.query,
                assistant_content=response.answer,
                intent=response.intent,
                sources=[s.model_dump() for s in response.sources],
                citations=[c.model_dump() for c in response.citations],
                abolishment_warnings=list(response.abolishment_warnings),
                llm_usage=response.llm_usage.model_dump() if response.llm_usage else None,
                elapsed_ms=response.elapsed_ms,
            )
        except (ValueError, Exception) as e:
            logger.warning("ask-v2 persistence failed for session %s: %r", request.session_id, e)

    return response


# ---- /legal-ai/ask-v2/stream: SSE streaming variant ---------------------
# Same logic as /ask-v2 but streams typed events so the user sees Albanian
# tokens within a few seconds instead of waiting for the full ~2-3 min
# generation. Browsers' EventSource doesn't support POST or Authorization
# headers, so the frontend uses fetch() + ReadableStream to consume this.
#
# Event types (each emitted as `event: <name>\ndata: <json>\n\n`):
#   route                — intent + reason (fires immediately after classify)
#   sources              — retrieved chunks (fires after retrieval, before LLM)
#   abolishment_warnings — warning strings (when applicable)
#   delta                — { "text": "..." } per LLM token chunk
#   done                 — final validated answer + citations + llm_usage + elapsed_ms
#   error                — { code, message } on failure (replaces `done`)

from fastapi.responses import StreamingResponse  # noqa: E402
from app.ai.pipeline import answer_stream as pipeline_answer_stream  # noqa: E402
from app.ai.v2_adapter import adapt_source_for_v2  # noqa: E402


def _sse_format(event: str, payload: Any) -> str:
    """Format one SSE event. Newlines in the JSON are not a problem because
    we serialize compactly (no indentation), but we still need the trailing
    blank line to flush the event.
    """
    import json as _json
    return f"event: {event}\ndata: {_json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/ask-v2/stream")
async def ask_legal_question_v2_stream(
    request: AskV2Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE streaming version of /ask-v2.

    Same request body. Returns `text/event-stream`. Errors that happen
    BEFORE the stream starts (auth, validation) come back as normal HTTP
    errors; errors AFTER the stream has begun are emitted as an `error`
    SSE event because we can't change the response status code mid-stream.
    """
    history = list(request.conversation_history or [])[-6:]
    session_uuid: UUID | None = None
    if request.session_id:
        try:
            session_uuid = UUID(request.session_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid session_id",
            )

    async def event_generator():
        accumulated_answer = ""
        final_payload: dict[str, Any] | None = None

        # Cloud Run's Google Front End buffers small response chunks up to
        # ~32KB before flushing to the client. SSE events are usually <200
        # bytes each, so without forcing a flush, the user waits the full
        # 2-3 min for the LLM to finish and only then gets all events in
        # one burst. Worse, the long pause without bytes can trigger an
        # intermediate connection close, surfacing as a "transport error"
        # in the browser and a generic "gabim teknik" message to the user.
        #
        # Fix: emit a 2KB padding comment immediately. SSE comments
        # (lines starting with `:`) are valid per the spec and ignored by
        # all SSE consumers, but the bytes count toward the flush threshold
        # so the GFE forwards everything we've buffered so far. Yield once
        # to the event loop after the padding so the bytes actually flush
        # before we go off to do the slow retrieval work.
        yield ":" + (" " * 2048) + "\n\n"
        import asyncio as _asyncio_yield
        await _asyncio_yield.sleep(0)

        try:
            async for event_name, payload in pipeline_answer_stream(
                request.query,
                namespace=request.namespace,
                use_llm=request.use_llm,
                conversation_history=history if history else None,
            ):
                # Mirror v2_adapter shape for the `sources` event so the
                # frontend's existing AvokAiSourceCard renderer works without
                # branching on whether the data came from /ask-v2 or
                # /ask-v2/stream.
                if event_name == "sources":
                    raw_sources = payload.get("sources", [])
                    enriched = [adapt_source_for_v2(s) for s in raw_sources]
                    yield _sse_format("sources", {"sources": [e.model_dump() for e in enriched]})
                elif event_name == "delta":
                    text = payload.get("text", "")
                    accumulated_answer += text
                    yield _sse_format("delta", {"text": text})
                elif event_name == "done":
                    final_payload = payload
                    # Mirror the v2 sidebar shape for sources in `done` too,
                    # so a client that only listens to `done` (e.g. an eval
                    # harness) gets the same structure as the legacy endpoint.
                    raw_sources = payload.get("sources") or []
                    enriched_sources = [adapt_source_for_v2(s).model_dump() for s in raw_sources]
                    out = {
                        "answer": payload.get("answer", ""),
                        "intent": payload.get("intent"),
                        "sources": enriched_sources,
                        "citations": payload.get("citations", []),
                        "abolishment_warnings": payload.get("abolishment_warnings", []),
                        "llm_usage": payload.get("llm_usage"),
                        "elapsed_ms": payload.get("elapsed_ms", 0),
                        "route_trace": payload.get("route_trace", {}),
                    }
                    yield _sse_format("done", out)
                else:
                    yield _sse_format(event_name, payload)
        except Exception as e:
            logger.exception("ask-v2/stream: pipeline error")
            yield _sse_format(
                "error",
                {"code": "PIPELINE_ERROR", "message": repr(e)},
            )

        # Best-effort persistence — runs after the stream has finished
        # producing events but before the connection closes. Same rule as
        # the non-streaming endpoint: storage failure must not be visible
        # to the user (their answer is already on screen).
        if session_uuid is not None and final_payload is not None:
            try:
                await chat_crud.append_turn(
                    db,
                    session_id=session_uuid,
                    user_id=current_user.id,
                    user_content=request.query,
                    assistant_content=final_payload.get("answer") or accumulated_answer,
                    intent=final_payload.get("intent"),
                    sources=final_payload.get("sources") or [],
                    citations=final_payload.get("citations") or [],
                    abolishment_warnings=list(final_payload.get("abolishment_warnings") or []),
                    llm_usage=final_payload.get("llm_usage"),
                    elapsed_ms=final_payload.get("elapsed_ms"),
                )
            except Exception as e:
                logger.warning("ask-v2/stream persistence failed for session %s: %r", session_uuid, e)

    # `X-Accel-Buffering: no` disables nginx-style proxy buffering. Cloud
    # Run's Google Front End respects it and flushes events as they're
    # produced; without it, GFE may buffer up to a few seconds of output.
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---- /legal-ai/chat/sessions: persistent chat history --------------------
# Per-user private chat sessions. Routing decisions: auto-create on first
# message (frontend POSTs an empty session, then sends turns via /ask-v2
# with the new session_id). Forever retention, manual delete only. Future
# extension: shared/team sessions via a join table on chat_sessions.

@router.post("/chat/sessions", response_model=ChatSessionSummary, status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    body: Optional[ChatSessionCreate] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new (empty) chat session for the current user.

    The body is optional. If omitted, the session is created with the default
    Albanian placeholder title ("Bisedë e re"), to be replaced automatically
    when the first user message is appended by /ask-v2.
    """
    title = body.title if body else None
    session = await chat_crud.create_session(db, current_user.id, title=title)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create chat session",
        )
    return session


@router.get("/chat/sessions", response_model=List[ChatSessionSummary])
async def list_chat_sessions(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's chat sessions, newest activity first."""
    return await chat_crud.list_sessions(db, current_user.id)


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a session with all its messages.

    404 if the session doesn't exist or isn't owned by the caller (no
    information leak about other users' sessions).
    """
    session = await chat_crud.get_session(db, session_id, current_user.id, with_messages=True)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.patch("/chat/sessions/{session_id}", response_model=ChatSessionSummary)
async def rename_chat_session(
    session_id: UUID,
    body: ChatSessionUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a chat session."""
    session = await chat_crud.update_session_title(db, session_id, current_user.id, body.title)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and all its messages (cascade)."""
    ok = await chat_crud.delete_session(db, session_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return None


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