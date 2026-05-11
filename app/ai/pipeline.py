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
    res = _openai_embeddings().embeddings.create(model=EMBED_MODEL, input=[query])
    return res.data[0].embedding


# ----- retrieval primitives ----------------------------------------------


def _semantic_retrieve(query: str, namespace: str) -> list[dict[str, Any]]:
    """Dense top-200 from Pinecone, rescored to top-K by query-time BM25."""
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
    return bm25_rescore(query, pool, alpha=RESCORE_ALPHA, top_k=TOP_K)


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
            answer=GREETING_RESPONSE,
            route_trace=trace,
            elapsed_ms=int((time.time() - t0) * 1000),
        )
    if decision.intent == "out_of_scope":
        return AnswerResult(
            query=query,
            intent="out_of_scope",
            answer=OUT_OF_SCOPE_RESPONSE,
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
        deterministic_answer = sources[0]["content"] if sources else (
            f"Statusi i Ligjit {decision.citation.law_number} nuk gjendet në regjistrin e shfuqizimit."  # type: ignore[union-attr]
        )
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
        final_answer = (
            "Nuk kam informacion të mjaftueshëm në bazën e të dhënave për "
            "të dhënë një përgjigje të verifikueshme për këtë pyetje."
        )
    else:
        try:
            from app.ai.llm import complete

            messages = build_messages(
                question=query,
                sources=sources,
                abolishment_warnings=warnings,
                conversation_history=conversation_history,
                primary_source_id=primary_id,
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
            final_answer = (
                "Ndodhi një gabim teknik gjatë gjenerimit të përgjigjes. "
                "Burimet e marra janë në dispozicion më poshtë për referencë."
            )

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

    return AnswerResult(
        query=query,
        intent=decision.intent,
        answer=annotated_answer,
        sources=sources,
        citations=citation_records,
        citation_summary=citation_summary,
        abolishment_warnings=warnings,
        route_trace=trace,
        elapsed_ms=int((time.time() - t0) * 1000),
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


__all__ = ["AnswerResult", "answer"]
