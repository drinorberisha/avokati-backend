"""
End-to-end AvokAI answer pipeline.

Single entry point `answer(query, ...)` that:

  1. Routes the query (greeting / status / citation / semantic / oos)
  2. Retrieves the right context for that route:
       - status        → abolishment registry, synthetic chunks
       - citation      → metadata-filter lookup against Pinecone
       - semantic      → dense top-200 + BM25 rescore (α=0.2)
  3. For semantic: calls DeepSeek-V4-Pro with the Albanian prompt
  4. Validates citations the LLM emitted against the retrieved sources
  5. Annotates unverified citations with `⚠️ i paverifikuar`
  6. Returns a single dict with answer, sources, costs, route trace

Designed to be backend-import-free for environments where
`app.core.config.Settings` doesn't load cleanly (the Phase 0 finding).
Talks to Pinecone + OpenAI embeddings + DeepSeek directly via env vars.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from app.ai.abolishment import (
    AbolishmentRegistry,
    render_synthetic_chunks,
)
from app.ai.bm25_rescore import rescore as bm25_rescore
from app.ai.citation import lookup_by_citation
from app.ai.citation_validator import (
    annotate_unverified,
    extract_citations,
    summarize as summarize_citations,
    validate_against_sources,
)
from app.ai.prompts.legal_qa_sq import build_messages
from app.ai.reranker import (
    RERANK_POOL_SIZE,
    is_enabled as reranker_enabled,
    rerank as cross_encoder_rerank,
)
from app.ai.router import (
    GREETING_RESPONSE,
    OUT_OF_SCOPE_RESPONSE,
    RoutingDecision,
    classify,
)


DEFAULT_NAMESPACE = os.environ.get("PINECONE_NAMESPACE_V2", "default_v2")
EMBED_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
DENSE_POOL_SIZE = 200
RESCORE_ALPHA = 0.2
TOP_K = 5  # what the LLM gets


@dataclass
class AnswerResult:
    query: str
    intent: str
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    citation_summary: dict[str, Any] = field(default_factory=dict)
    abolishment_warnings: list[str] = field(default_factory=list)
    route_trace: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    llm_usage: dict[str, Any] | None = None


_MESSAGES = {
    "sq": {
        "greeting": GREETING_RESPONSE,
        "out_of_scope": OUT_OF_SCOPE_RESPONSE,
        "insufficient_context": (
            "Nuk kam informacion të mjaftueshëm në bazën e të dhënave për "
            "të dhënë një përgjigje të verifikueshme për këtë pyetje."
        ),
        "generation_error": (
            "Ndodhi një gabim teknik gjatë gjenerimit të përgjigjes. "
            "Burimet e marra janë në dispozicion më poshtë për referencë."
        ),
    },
    "en": {
        "greeting": (
            "Hello! I am AvokAI, a legal assistant for Kosovo legislation. "
            "How can I help with a legal question today? You can ask about a "
            "specific article, the status of a law, or legal provisions on a topic."
        ),
        "out_of_scope": (
            "Sorry, I specialize only in Kosovo legislation and cannot help with "
            "questions outside that area. Please ask a legal question."
        ),
        "insufficient_context": (
            "I do not have enough information in the database to provide a "
            "verifiable answer to this question."
        ),
        "generation_error": (
            "A technical error occurred while generating the answer. The retrieved "
            "sources are available below for reference."
        ),
    },
}


def _normalize_response_language(language: str | None) -> str:
    return "en" if language == "en" else "sq"


def _localized_message(key: str, language: str) -> str:
    lang = _normalize_response_language(language)
    return _MESSAGES[lang].get(key) or _MESSAGES["sq"][key]


def _status_answer(info: Any, language: str) -> str:
    if _normalize_response_language(language) != "en":
        return ""
    if info.status == "fully_abolished":
        ab_list = ", ".join(
            (r.get("abolishing_law") or {}).get("law_number") or "?" for r in info.abolished_by
        )
        return f"Law {info.law_number} has been fully abolished. It was replaced by law/laws: {ab_list}."
    if info.status == "partially_abolished":
        ab_list = ", ".join(
            (r.get("abolishing_law") or {}).get("law_number") or "?" for r in info.abolished_by
        )
        return f"Law {info.law_number} has been partially abolished. Parts of it were replaced by: {ab_list}."
    if info.status == "abolisher":
        old_list = ", ".join(
            (r.get("abolished_law") or {}).get("law_number") or "?" for r in info.abolishes
        )
        return f"Law {info.law_number} is currently in force and has abolished: {old_list}."
    return f"The status of Law {info.law_number} was not found in the abolishment relations registry."


# ----- per-call infra (lazy & cached) ------------------------------------

_clients: dict[str, Any] = {}


def _pinecone_index():
    if "index" not in _clients:
        from pinecone import Pinecone

        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        _clients["index"] = pc.Index(os.environ.get("PINECONE_INDEX_NAME", "lawvector"))
    return _clients["index"]


def _openai_embeddings():
    if "openai" not in _clients:
        from openai import OpenAI

        _clients["openai"] = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _clients["openai"]


def _dummy_vector():
    """Pinecone insists on a vector even for filter-only queries.

    We embed a single placeholder once per process and reuse — the score
    column is meaningless on filter queries anyway because we re-rank
    locally.
    """
    if "dummy" not in _clients:
        emb = _openai_embeddings().embeddings.create(
            model=EMBED_MODEL, input=["placeholder"]
        )
        _clients["dummy"] = emb.data[0].embedding
    return _clients["dummy"]


def _embed_query(query: str) -> list[float]:
    """Embed the query with OpenAI. Fails loudly on any provider error.

    The hash-based LocalEmbeddingProvider fallback was deleted in May 2026
    because it silently poisoned the index with semantic noise on quota
    errors. If OpenAI is unavailable, we now raise
    `EmbeddingUnavailableError`, which the /ask-v2 route turns into a 503
    with a clear Albanian-language message for the user.
    """
    from app.ai.embedding.providers import EmbeddingUnavailableError
    try:
        res = _openai_embeddings().embeddings.create(model=EMBED_MODEL, input=[query])
        return res.data[0].embedding
    except Exception as e:
        raise EmbeddingUnavailableError(f"OpenAI embedding call failed: {e!r}") from e


# ----- retrieval primitives ----------------------------------------------


def _semantic_retrieve(query: str, namespace: str) -> list[dict[str, Any]]:
    """Dense top-200 → BM25-rescored top-20 → cross-encoder reranked top-K.

    The reranker is applied to the BM25-rescored pool, not the dense top-200
    directly: BM25 cuts lexical noise cheaply, and cross-encoder inference is
    O(n) in candidate count. We only spend the reranker budget on candidates
    BM25 already thinks are relevant.

    If the reranker is disabled or fails to load, we degrade gracefully:
    `cross_encoder_rerank` returns the input order unchanged.
    """
    index = _pinecone_index()
    emb = _embed_query(query)
    res = index.query(
        namespace=namespace,
        vector=emb,
        top_k=DENSE_POOL_SIZE,
        include_metadata=True,
    )
    pool: list[dict[str, Any]] = []
    for m in res.matches:
        meta = dict(m.metadata or {})
        pool.append(
            {
                "id": m.id,
                "metadata": meta,
                "score": float(m.score) if m.score is not None else 0.0,
                "content": meta.get("content") or meta.get("text") or "",
            }
        )

    # Wide pool for the reranker if enabled; otherwise just the final TOP_K.
    bm25_top_k = RERANK_POOL_SIZE if reranker_enabled() else TOP_K
    bm25_results = bm25_rescore(query, pool, alpha=RESCORE_ALPHA, top_k=bm25_top_k)

    if not reranker_enabled():
        return bm25_results

    return cross_encoder_rerank(query, bm25_results, top_k=TOP_K)


def _citation_retrieve(decision: RoutingDecision, namespace: str) -> dict[str, Any]:
    assert decision.citation is not None
    return lookup_by_citation(
        citation=decision.citation,
        pinecone_index=_pinecone_index(),
        namespace=namespace,
        dummy_vector=_dummy_vector(),
        top_k=TOP_K,
    )


# ----- abolishment annotation across any retrieved chunk ------------------


def _abolishment_warnings(
    sources: list[dict[str, Any]], registry: AbolishmentRegistry
) -> list[str]:
    """For each retrieved source, check if its parent law is abolished and
    return a short Albanian warning string per affected law (deduped).
    """
    seen: set[str] = set()
    warnings: list[str] = []
    for src in sources:
        meta = src.get("metadata") or src.get("document_metadata") or {}
        law = (meta.get("law_number") or "").strip()
        if not law or law in seen:
            continue
        seen.add(law)
        info = registry.lookup(law)
        if info.status not in ("fully_abolished", "partially_abolished"):
            continue
        ab_list = ", ".join(
            (r.get("abolishing_law") or {}).get("law_number") or "?"
            for r in info.abolished_by
        )
        verb = (
            "është shfuqizuar plotësisht"
            if info.status == "fully_abolished"
            else "është shfuqizuar pjesërisht"
        )
        warnings.append(f"Ligji {law} {verb} nga: {ab_list}")
    return warnings


# ----- main entry point --------------------------------------------------


def answer(
    query: str,
    *,
    namespace: str | None = None,
    use_llm: bool = True,
    conversation_history: list[dict[str, str]] | None = None,
    response_language: str = "sq",
) -> AnswerResult:
    """One call from query to validated answer.

    Args:
        query: user's question (any language; we reply in Albanian).
        namespace: Pinecone namespace to query. Defaults to `default_v2`.
        use_llm: if False, skip the LLM call and return raw retrieval.
            Useful for retrieval-only eval and for environments where
            DEEPSEEK_API_KEY isn't configured.
        conversation_history: optional prior turns for the prompt.

    Returns AnswerResult — even on errors, never raises into the caller's
    request handler. LLM/retrieval failures are surfaced as text in the
    `answer` field.
    """
    t0 = time.time()
    ns = namespace or DEFAULT_NAMESPACE
    lang = _normalize_response_language(response_language)
    decision = classify(query)
    trace: dict[str, Any] = {
        "intent": decision.intent,
        "reason": decision.reason,
        "namespace": ns,
        "citation_parsed": (
            {
                "law_number": decision.citation.law_number,
                "article_number": decision.citation.article_number,
            }
            if decision.citation
            else None
        ),
    }

    # No-retrieval routes return immediately.
    if decision.intent == "greeting":
        return AnswerResult(
            query=query,
            intent="greeting",
            answer=_localized_message("greeting", lang),
            route_trace=trace,
            elapsed_ms=int((time.time() - t0) * 1000),
        )
    if decision.intent == "out_of_scope":
        return AnswerResult(
            query=query,
            intent="out_of_scope",
            answer=_localized_message("out_of_scope", lang),
            route_trace=trace,
            elapsed_ms=int((time.time() - t0) * 1000),
        )

    # Retrieval
    registry = AbolishmentRegistry.get()
    sources: list[dict[str, Any]] = []
    deterministic_answer: str | None = None

    if decision.intent == "status_lookup":
        info = registry.lookup(decision.citation.law_number)  # type: ignore[union-attr]
        sources = render_synthetic_chunks(info)
        # Status questions answer themselves from the registry — no LLM needed.
        deterministic_answer = _status_answer(info, lang) or (sources[0]["content"] if sources else (
            f"Statusi i Ligjit {decision.citation.law_number} nuk gjendet në regjistrin e shfuqizimit."  # type: ignore[union-attr]
        ))
        trace["abolishment_status"] = info.status

    elif decision.intent == "citation_lookup":
        lookup = _citation_retrieve(decision, ns)
        if lookup["matches"]:
            sources = [
                {
                    "id": m["id"],
                    "metadata": m["metadata"],
                    "score": m["score"],
                    "content": m["content"],
                }
                for m in lookup["matches"]
            ]
            trace["citation_match_quality"] = lookup["article_match_quality"]
            trace["matched_law_variant"] = lookup["matched_law_variant"]
        else:
            trace["citation_miss"] = True
            # Fall back to semantic so the user gets *something* useful.
            decision = RoutingDecision(
                "semantic_question", decision.citation, "citation_miss_fallback"
            )

    if decision.intent == "semantic_question" and not sources:
        sources = _semantic_retrieve(query, ns)

    # Cross-reference abolishment for ANY retrieved chunk (even when the
    # primary route wasn't status). A semantic hit on an abolished law
    # should still warn the user.
    warnings = _abolishment_warnings(sources, registry)

    # Generation
    final_answer: str
    llm_usage: dict[str, Any] | None = None

    # For citation_lookup we know the first source IS the asked article
    # (lookup_by_citation guarantees this in `exact_start` quality results).
    # Pass that ID to the prompt builder so the LLM doesn't drift to a
    # neighboring article that's only there for context.
    primary_id: str | None = None
    if (
        decision.intent == "citation_lookup"
        and trace.get("citation_match_quality") == "exact_start"
        and sources
    ):
        primary_id = sources[0].get("id")

    if deterministic_answer is not None:
        final_answer = deterministic_answer
        # Only prepend the abolishment warning if the deterministic answer
        # doesn't ALREADY say it. The status_lookup verdict already includes
        # the abolisher law, so prepending duplicates the same fact verbatim.
        # We detect this by checking whether the abolisher law numbers from
        # the warning appear anywhere in the verdict — if they do, skip the
        # prepended banner entirely.
        if warnings and not _warning_already_in_text(final_answer, warnings):
            final_answer = "⚠️ KUJDES: " + warnings[0] + ". " + final_answer
    elif not use_llm:
        # Retrieval-only mode (used by retrieval eval). Stub out generation.
        final_answer = "[retrieval-only mode; no LLM answer generated]"
    elif not sources:
        final_answer = _localized_message("insufficient_context", lang)
    else:
        try:
            from app.ai.llm import complete

            messages = build_messages(
                question=query,
                sources=sources,
                abolishment_warnings=warnings,
                conversation_history=conversation_history,
                primary_source_id=primary_id,
                response_language=lang,
            )
            result = complete(messages, temperature=0.1)
            final_answer = result.text
            llm_usage = {
                "model": result.model,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "cached_tokens": result.cached_tokens,
                "usd_cost_estimate": round(result.usd_cost_estimate(), 6),
                "finish_reason": result.finish_reason,
            }
        except Exception as e:
            trace["llm_error"] = repr(e)
            final_answer = _localized_message("generation_error", lang)

    # Validation. Treat any law referenced by abolishment context as
    # authoritative: the model legitimately mentions abolisher laws (and
    # status-route synthetic chunks) that aren't necessarily in the
    # retrieved-from-Pinecone set, but they ARE in our authoritative
    # registry and should not be flagged as hallucinated.
    authoritative_laws: set[str] = set()
    for s in sources:
        meta = s.get("metadata") or s.get("document_metadata") or {}
        if meta.get("synthetic"):
            law = (meta.get("law_number") or "").strip().upper().replace(" ", "")
            if law:
                authoritative_laws.add(law)
    # Also pull abolisher law numbers out of the warning strings — those
    # come from `abolishment_relations.json` which IS authoritative even
    # when the abolisher's chunks weren't retrieved.
    import re as _re_pipe
    _law_re = _re_pipe.compile(r"(?:KUV-)?\d{1,2}/L-\d{1,4}[A-Za-z0-9-]*|(?:19|20)\d{2}/\d{1,3}")
    for w in warnings:
        for m in _law_re.finditer(w):
            authoritative_laws.add(m.group(0).upper().replace(" ", ""))

    extracted = extract_citations(final_answer)
    validated = validate_against_sources(extracted, sources, authoritative_laws=authoritative_laws)
    annotated_answer = annotate_unverified(final_answer, validated)
    citation_summary = summarize_citations(validated)
    citation_records = [
        {
            "raw": v.citation.raw,
            "law_number": v.citation.law_number,
            "article_number": v.citation.article_number,
            "verified": v.verified,
            "matched_source_id": v.matched_source_id,
        }
        for v in validated
    ]

    elapsed_ms_total = int((time.time() - t0) * 1000)

    # Structured per-query log line for prod cost/latency observability.
    # Cloud Run already collects stdout into Cloud Logging; this line is
    # picked up there and can be piped into BigQuery via a log sink for
    # ad-hoc analysis (P50/P95 latency, $/query, route mix, etc.) without
    # standing up a dashboard yet. Keep the schema stable so downstream
    # queries don't break — add fields, don't rename.
    try:
        import hashlib as _hashlib
        import json as _json
        _log_payload = {
            "event": "avokai_query",
            "intent": decision.intent,
            "route_reason": decision.reason,
            "namespace": ns,
            "query_hash": _hashlib.sha1((query or "").encode("utf-8")).hexdigest()[:12],
            "query_len": len(query or ""),
            "sources_count": len(sources),
            "abolishment_warnings_count": len(warnings),
            "citations_total": citation_summary.get("total", 0) if isinstance(citation_summary, dict) else 0,
            "citations_verified": citation_summary.get("verified", 0) if isinstance(citation_summary, dict) else 0,
            "elapsed_ms_total": elapsed_ms_total,
            "llm_model": (llm_usage or {}).get("model"),
            "llm_prompt_tokens": (llm_usage or {}).get("prompt_tokens"),
            "llm_completion_tokens": (llm_usage or {}).get("completion_tokens"),
            "llm_cached_tokens": (llm_usage or {}).get("cached_tokens"),
            "llm_usd_cost": (llm_usage or {}).get("usd_cost_estimate"),
            "llm_used": llm_usage is not None,
            "had_llm_error": "llm_error" in trace,
        }
        print("avokai_query_log " + _json.dumps(_log_payload, separators=(",", ":")))
    except Exception:
        # Logging is best-effort and must never fail the request.
        pass

    return AnswerResult(
        query=query,
        intent=decision.intent,
        answer=annotated_answer,
        sources=sources,
        citations=citation_records,
        citation_summary=citation_summary,
        abolishment_warnings=warnings,
        route_trace=trace,
        elapsed_ms=elapsed_ms_total,
        llm_usage=llm_usage,
    )


def _warning_already_in_text(text: str, warnings: list[str]) -> bool:
    """Heuristic: every law number mentioned in any warning already appears
    verbatim in `text`. Used to avoid duplicating the abolishment banner
    when the deterministic verdict already names the abolisher.
    """
    import re as _re
    if not warnings:
        return False
    law_pat = _re.compile(r"\b(?:KUV-)?\d{1,2}/L-\d{1,4}[A-Za-z0-9-]*|\b(?:19|20)\d{2}/\d{1,3}\b")
    text_canon = text.upper().replace(" ", "")
    for w in warnings:
        for m in law_pat.finditer(w):
            law = m.group(0).upper().replace(" ", "")
            if law not in text_canon:
                return False
    return True


# ----- streaming variant (for the SSE /ask-v2/stream endpoint) -----------
#
# Async generator that yields typed events as the pipeline progresses, so
# the frontend can render route + sources within ~2s and start showing
# Albanian tokens within ~3-5s instead of waiting for the full ~2-3 min
# generation.
#
# Event shape: `("event_name", payload_dict)`. The frontend SSE consumer
# turns each tuple into a `event: name\ndata: <json>\n\n` block.
#
# Events fired (in order; not all fire for every route):
#   route                 — always; carries intent + reason + namespace + parsed citation
#   sources               — when retrieval produced any chunks (semantic, citation_lookup, status_lookup)
#   abolishment_warnings  — when warnings were generated
#   delta                 — repeatedly, when LLM streams tokens (semantic + citation_lookup paths)
#   done                  — always; carries final validated answer, citations, llm_usage, elapsed_ms
#   error                 — replaces `done` when the pipeline raises (e.g. embedding unavailable)


async def answer_stream(
    query: str,
    *,
    namespace: str | None = None,
    use_llm: bool = True,
    conversation_history: list[dict[str, str]] | None = None,
    response_language: str = "sq",
):
    """Streaming variant of `answer()` for the SSE endpoint.

    Yields `(event_name, payload)` tuples. Same logic as `answer()` but
    routes that have an LLM call stream the tokens via DeepSeek's
    `stream=True` mode instead of waiting for the full response.

    Retrieval primitives are still sync (Pinecone, BM25, reranker) — they
    run inline because each is fast enough (< 4s combined) to not bother
    offloading to a thread for a single user.

    Errors are surfaced as `("error", {"message": ...})` rather than raised
    — the SSE endpoint cannot reasonably bubble an exception once the
    response stream has started.
    """
    import asyncio  # local to keep top-of-file imports unchanged

    t0 = time.time()
    ns = namespace or DEFAULT_NAMESPACE
    lang = _normalize_response_language(response_language)
    decision = classify(query)
    trace: dict[str, Any] = {
        "intent": decision.intent,
        "reason": decision.reason,
        "namespace": ns,
        "citation_parsed": (
            {
                "law_number": decision.citation.law_number,
                "article_number": decision.citation.article_number,
            }
            if decision.citation
            else None
        ),
    }
    yield ("route", dict(trace))

    # --- non-LLM short-circuit paths ----------------------------------
    if decision.intent == "greeting":
        elapsed = int((time.time() - t0) * 1000)
        yield (
            "done",
            {
                "answer": _localized_message("greeting", lang),
                "sources": [],
                "citations": [],
                "citation_summary": {},
                "abolishment_warnings": [],
                "intent": "greeting",
                "elapsed_ms": elapsed,
                "llm_usage": None,
                "route_trace": trace,
            },
        )
        return

    if decision.intent == "out_of_scope":
        elapsed = int((time.time() - t0) * 1000)
        yield (
            "done",
            {
                "answer": _localized_message("out_of_scope", lang),
                "sources": [],
                "citations": [],
                "citation_summary": {},
                "abolishment_warnings": [],
                "intent": "out_of_scope",
                "elapsed_ms": elapsed,
                "llm_usage": None,
                "route_trace": trace,
            },
        )
        return

    # --- retrieval -----------------------------------------------------
    registry = AbolishmentRegistry.get()
    sources: list[dict[str, Any]] = []
    deterministic_answer: str | None = None

    try:
        if decision.intent == "status_lookup":
            info = registry.lookup(decision.citation.law_number)  # type: ignore[union-attr]
            sources = render_synthetic_chunks(info)
            deterministic_answer = _status_answer(info, lang) or (sources[0]["content"] if sources else (
                f"Statusi i Ligjit {decision.citation.law_number} nuk gjendet në regjistrin e shfuqizimit."  # type: ignore[union-attr]
            ))
            trace["abolishment_status"] = info.status

        elif decision.intent == "citation_lookup":
            # _citation_retrieve is sync (Pinecone metadata filter). Fast
            # enough to run inline.
            lookup = await asyncio.to_thread(_citation_retrieve, decision, ns)
            if lookup["matches"]:
                sources = [
                    {
                        "id": m["id"],
                        "metadata": m["metadata"],
                        "score": m["score"],
                        "content": m["content"],
                    }
                    for m in lookup["matches"]
                ]
                trace["citation_match_quality"] = lookup["article_match_quality"]
                trace["matched_law_variant"] = lookup["matched_law_variant"]
            else:
                trace["citation_miss"] = True
                decision = RoutingDecision(
                    "semantic_question", decision.citation, "citation_miss_fallback"
                )

        if decision.intent == "semantic_question" and not sources:
            sources = await asyncio.to_thread(_semantic_retrieve, query, ns)
    except Exception as e:
        # Most likely EmbeddingUnavailableError. Surface clearly and stop.
        yield (
            "error",
            {
                "code": getattr(e, "code", "RETRIEVAL_FAILED"),
                "message": str(e) or "Retrieval failed",
            },
        )
        return

    warnings = _abolishment_warnings(sources, registry)
    if sources:
        # Emit only what the UI needs to render the sidebar — same shape as
        # the existing AskV2Response.sources after v2_adapter, but adapter
        # transforms aren't strictly needed here because the frontend just
        # passes them through to AvokAiSourceList.
        yield ("sources", {"sources": sources})
    if warnings:
        yield ("abolishment_warnings", {"warnings": warnings})

    # --- generation ----------------------------------------------------
    final_answer: str
    llm_usage: dict[str, Any] | None = None

    primary_id: str | None = None
    if (
        decision.intent == "citation_lookup"
        and trace.get("citation_match_quality") == "exact_start"
        and sources
    ):
        primary_id = sources[0].get("id")

    if deterministic_answer is not None:
        final_answer = deterministic_answer
        if warnings and not _warning_already_in_text(final_answer, warnings):
            final_answer = "⚠️ KUJDES: " + warnings[0] + ". " + final_answer
        # No LLM streaming; emit the full text as a single delta so the
        # frontend's incremental render code-path is uniform across routes.
        yield ("delta", {"text": final_answer})
    elif not use_llm:
        final_answer = "[retrieval-only mode; no LLM answer generated]"
        yield ("delta", {"text": final_answer})
    elif not sources:
        final_answer = _localized_message("insufficient_context", lang)
        yield ("delta", {"text": final_answer})
    else:
        try:
            from app.ai.llm import acomplete_stream

            messages = build_messages(
                question=query,
                sources=sources,
                abolishment_warnings=warnings,
                conversation_history=conversation_history,
                primary_source_id=primary_id,
                response_language=lang,
            )
            buf_parts: list[str] = []
            async for event_type, payload in acomplete_stream(messages, temperature=0.1):
                if event_type == "delta":
                    buf_parts.append(payload)
                    yield ("delta", {"text": payload})
                elif event_type == "done":
                    result = payload  # CompletionResult
                    llm_usage = {
                        "model": result.model,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        "cached_tokens": result.cached_tokens,
                        "usd_cost_estimate": round(result.usd_cost_estimate(), 6),
                        "finish_reason": result.finish_reason,
                    }
            final_answer = "".join(buf_parts)
        except Exception as e:
            trace["llm_error"] = repr(e)
            final_answer = _localized_message("generation_error", lang)
            yield ("delta", {"text": final_answer})

    # --- validation + final event -------------------------------------
    authoritative_laws: set[str] = set()
    for s in sources:
        meta = s.get("metadata") or s.get("document_metadata") or {}
        if meta.get("synthetic"):
            law = (meta.get("law_number") or "").strip().upper().replace(" ", "")
            if law:
                authoritative_laws.add(law)
    import re as _re_pipe
    _law_re = _re_pipe.compile(r"(?:KUV-)?\d{1,2}/L-\d{1,4}[A-Za-z0-9-]*|(?:19|20)\d{2}/\d{1,3}")
    for w in warnings:
        for m in _law_re.finditer(w):
            authoritative_laws.add(m.group(0).upper().replace(" ", ""))

    extracted = extract_citations(final_answer)
    validated = validate_against_sources(extracted, sources, authoritative_laws=authoritative_laws)
    annotated_answer = annotate_unverified(final_answer, validated)
    citation_summary = summarize_citations(validated)
    citation_records = [
        {
            "raw": v.citation.raw,
            "law_number": v.citation.law_number,
            "article_number": v.citation.article_number,
            "verified": v.verified,
            "matched_source_id": v.matched_source_id,
        }
        for v in validated
    ]

    elapsed_ms_total = int((time.time() - t0) * 1000)

    # Emit the same structured cost/latency log line as the non-streaming
    # `answer()` so we get consistent observability across endpoints.
    try:
        import hashlib as _hashlib
        import json as _json
        _log_payload = {
            "event": "avokai_query",
            "intent": decision.intent,
            "route_reason": decision.reason,
            "namespace": ns,
            "query_hash": _hashlib.sha1((query or "").encode("utf-8")).hexdigest()[:12],
            "query_len": len(query or ""),
            "sources_count": len(sources),
            "abolishment_warnings_count": len(warnings),
            "citations_total": citation_summary.get("total", 0) if isinstance(citation_summary, dict) else 0,
            "citations_verified": citation_summary.get("verified", 0) if isinstance(citation_summary, dict) else 0,
            "elapsed_ms_total": elapsed_ms_total,
            "llm_model": (llm_usage or {}).get("model"),
            "llm_prompt_tokens": (llm_usage or {}).get("prompt_tokens"),
            "llm_completion_tokens": (llm_usage or {}).get("completion_tokens"),
            "llm_cached_tokens": (llm_usage or {}).get("cached_tokens"),
            "llm_usd_cost": (llm_usage or {}).get("usd_cost_estimate"),
            "llm_used": llm_usage is not None,
            "had_llm_error": "llm_error" in trace,
            "streamed": True,
        }
        print("avokai_query_log " + _json.dumps(_log_payload, separators=(",", ":")))
    except Exception:
        pass

    yield (
        "done",
        {
            "answer": annotated_answer,
            "sources": sources,
            "citations": citation_records,
            "citation_summary": citation_summary,
            "abolishment_warnings": warnings,
            "intent": decision.intent,
            "elapsed_ms": elapsed_ms_total,
            "llm_usage": llm_usage,
            "route_trace": trace,
        },
    )


__all__ = ["AnswerResult", "answer", "answer_stream"]
