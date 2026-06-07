from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, BackgroundTasks, status
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import uuid
from datetime import datetime
from pydantic import BaseModel

from app.core.database import get_db
from app.core.auth import get_current_active_user
from app.db.models import User
from app.core.consent import require_ai_consent

from app.ai.retrieval.langchain_service import langchain_service

router = APIRouter()
logger = logging.getLogger(__name__)


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
    _consent: None = Depends(require_ai_consent),
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
            response_language=request.response_language,
        )
    except EmbeddingUnavailableError as e:
        logger.error("ask-v2: embedding provider unavailable: %s", e)
        message = (
            "Search service is currently unavailable. Please try again in a few minutes."
            if request.response_language == "en"
            else (
                "Shërbimi i kërkimit nuk është i disponueshëm momentalisht. "
                "Ju lutemi provoni përsëri për pak minuta."
            )
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "EMBEDDING_UNAVAILABLE",
                "message": message,
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
    _consent: None = Depends(require_ai_consent),
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
        import asyncio as _asyncio

        accumulated_answer = ""
        final_payload: dict[str, Any] | None = None

        # Cloud Run's Google Front End buffers small response chunks before
        # forwarding them to the client. SSE events of <200 bytes each get
        # held until the buffer fills, which means the user waits the full
        # 2-3 min for the LLM to finish and only then sees the answer drop
        # in all at once. The initial 2KB padding (below) takes care of the
        # very first flush, but subsequent deltas accumulate again unless
        # we keep pushing bytes through.
        #
        # Strategy: run the pipeline in a background task feeding a queue,
        # and a heartbeat task that injects a padding-comment every 250 ms
        # so bytes flow continuously regardless of pipeline pauses. The
        # generator drains the queue and yields whatever lands.
        yield ":" + (" " * 2048) + "\n\n"
        await _asyncio.sleep(0)

        queue: _asyncio.Queue = _asyncio.Queue()
        SENTINEL_DONE = object()
        # 64-byte comment payload at 250 ms cadence (~250 B/sec). Enough to
        # keep the GFE TCP buffer ticking over so each subsequent real event
        # gets forwarded. SSE comments (`:...\n\n`) are ignored by clients.
        HEARTBEAT_PAYLOAD = ":" + ("h" * 64) + "\n\n"
        HEARTBEAT_INTERVAL = 0.25

        async def pipeline_task() -> None:
            try:
                async for event_name, payload in pipeline_answer_stream(
                    request.query,
                    namespace=request.namespace,
                    use_llm=request.use_llm,
                    conversation_history=history if history else None,
                    response_language=request.response_language,
                ):
                    await queue.put(("event", event_name, payload))
            except Exception as e:
                await queue.put(("error", e))
            finally:
                await queue.put(("sentinel", SENTINEL_DONE))

        async def heartbeat_task() -> None:
            try:
                while True:
                    await _asyncio.sleep(HEARTBEAT_INTERVAL)
                    await queue.put(("heartbeat", None))
            except _asyncio.CancelledError:
                return

        ptask = _asyncio.create_task(pipeline_task())
        hbtask = _asyncio.create_task(heartbeat_task())

        try:
            while True:
                item = await queue.get()
                kind = item[0]

                if kind == "sentinel":
                    break

                if kind == "heartbeat":
                    yield HEARTBEAT_PAYLOAD
                    continue

                if kind == "error":
                    e = item[1]
                    logger.exception("ask-v2/stream: pipeline error: %r", e)
                    yield _sse_format(
                        "error",
                        {"code": "PIPELINE_ERROR", "message": repr(e)},
                    )
                    continue

                # kind == "event"
                _, event_name, payload = item
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
        finally:
            hbtask.cancel()
            try:
                await hbtask
            except _asyncio.CancelledError:
                pass
            # Wait for pipeline task to finish naturally — if it raised, it
            # already pushed an error item and we yielded it above.
            try:
                await ptask
            except Exception:
                pass

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
