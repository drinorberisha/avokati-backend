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
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.ai.abolishment import (
    AbolishmentRegistry,
    render_synthetic_chunks,
)
from app.ai.bm25_rescore import rescore as bm25_rescore
from app.ai.citation import lookup_by_citation, parse_citation, ARTICLE_REF_PATTERN
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
    CLARIFY_MISSING_LAW_RESPONSE,
    CLARIFY_RESPONSE,
    GREETING_RESPONSE,
    OUT_OF_SCOPE_RESPONSE,
    RoutingDecision,
    incomplete_reference,
)
from app.ai.router_llm import classify_with_fallback
from app.ai.clarifier import generate_clarify_request
from app.ai.conversation import derive_context


DEFAULT_NAMESPACE = os.environ.get("PINECONE_NAMESPACE_V2", "default_v2")
EMBED_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
DENSE_POOL_SIZE = 200
RESCORE_ALPHA = 0.2
TOP_K = 5  # what the LLM gets
# Output cap for generation. This ceiling must cover BOTH the model's reasoning
# AND the visible answer — DeepSeek V4-Pro emits reasoning tokens before content,
# and they all count against completion_tokens. The old "2048 ≈ 6-8K chars" note
# was wrong: diacritic-heavy legal Albanian tokenizes at ~0.6 chars/token, so 2048
# tokens was only ~180 words — far below the prompt's ~500-word target. Measured on
# a real labor-law query: finish_reason="length" at exactly 2048, answer cut
# mid-word (and when reasoning alone exhausted the budget, an EMPTY answer). 8192
# comfortably covers reasoning + a complete ~500-word Albanian answer with citations.
# It's a CEILING, not a target — the prompt's "under 500 words" keeps typical answers
# short, so raising it only helps the answers that were being truncated. Env-tunable.
MAX_ANSWER_TOKENS = int(os.environ.get("AVOKAI_MAX_ANSWER_TOKENS", "8192"))

# Relevance floor (anti-fabrication gate). bge-reranker scores are sigmoid
# probabilities in [0,1]. If the BEST semantic source scores below the floor,
# the corpus almost certainly doesn't hold the answer — refuse instead of
# letting the LLM fabricate from low-relevance chunks (the konfederata →
# trade-union-law failure: a confident, "verified", wrong answer).
#
# Gated to SHORT queries only. A long fact-pattern (kazus) legitimately scores
# low per chunk because the single embedding is a blurry average of many issues
# — that's a query-decomposition problem, not an out-of-corpus signal, so
# refusing it would wrongly reject real legal questions. Measured separation:
# out-of-corpus shorts incl. konfederata < 0.40 < weakest legit atomic (0.441);
# civil kazus (>1200 chars) sit at 0.02-0.05 and are exempted by length.
RELEVANCE_FLOOR_ENABLED = os.environ.get("AVOKAI_RELEVANCE_FLOOR_ENABLED", "true").lower() in ("true", "1", "yes")
RELEVANCE_FLOOR = float(os.environ.get("AVOKAI_RELEVANCE_FLOOR", "0.40"))
RELEVANCE_FLOOR_MAX_QUERY_LEN = int(os.environ.get("AVOKAI_RELEVANCE_FLOOR_MAX_QUERY_LEN", "300"))
# Fallback floor on the RAW dense cosine (`_dense_score`), used ONLY when the
# cross-encoder rerank score is absent (reranker disabled or crashed at runtime).
# The reranker floor (0.40) is a bge sigmoid probability; this is a text-embedding-
# 3-large cosine, a different scale — relevant legal chunks land ~0.30-0.60,
# irrelevant ~0.10-0.25, so 0.28 is a conservative separator. NEEDS production
# calibration against the live index; tune via env without a redeploy. The point
# is that the anti-fabrication gate is NEVER silently off just because the reranker
# didn't run (audit finding G1).
RELEVANCE_FLOOR_DENSE = float(os.environ.get("AVOKAI_RELEVANCE_FLOOR_DENSE", "0.28"))

# Tier of the relevance gate that produced a decision — surfaced in route_trace so
# a degraded reranker is observable rather than silent.
#   rerank          → trusted bge score used
#   dense           → reranker absent; fell back to raw dense cosine
#   no_signal       → no relevance signal at all → fail-closed (refuse)
#   exempt_long     → query too long to gate per-chunk (see G2)
#   exempt_disabled → floor turned off / no sources
#   above           → a signal existed and cleared its floor
RelevanceTier = str


def _relevance_gate(query: str, sources: list[dict[str, Any]]) -> tuple[bool, RelevanceTier, float | None]:
    """Decide whether the best semantic source is too weak to answer from.

    Returns `(gated, tier, top_score)`. Layered so the gate is never silently
    off (G1): prefer the trusted rerank score; if the reranker didn't run, fall
    back to the raw dense cosine against a conservative floor; if there is no
    relevance signal at all, fail closed. Only fires on the semantic path and
    only for short queries — long fact patterns score low by nature and must not
    be refused on that basis (that exemption is G2, unchanged here).
    """
    if not RELEVANCE_FLOOR_ENABLED or not sources:
        return (False, "exempt_disabled", None)
    if len(query or "") > RELEVANCE_FLOOR_MAX_QUERY_LEN:
        return (False, "exempt_long", None)

    rerank_top = max(
        (float(s["_rerank_score"]) for s in sources if s.get("_rerank_score") is not None),
        default=None,
    )
    if rerank_top is not None:
        return (rerank_top < RELEVANCE_FLOOR, "rerank", rerank_top)

    # Reranker absent (disabled / crashed) — back off to the raw dense cosine.
    dense_top = max(
        (float(s["_dense_score"]) for s in sources if s.get("_dense_score") is not None),
        default=None,
    )
    if dense_top is not None:
        return (dense_top < RELEVANCE_FLOOR_DENSE, "dense", dense_top)

    # No trusted relevance signal at all → refuse rather than fabricate.
    return (True, "no_signal", None)


def _below_relevance_floor(query: str, sources: list[dict[str, Any]]) -> bool:
    """Back-compat boolean wrapper over `_relevance_gate`."""
    gated, _tier, _top = _relevance_gate(query, sources)
    return gated


# Long-query anti-fabrication gate (audit finding G2). `_relevance_gate` exempts
# queries > RELEVANCE_FLOOR_MAX_QUERY_LEN because a long fact-pattern's single
# averaged embedding scores low per chunk — refusing on that would reject real
# kazus. That exemption left long OUT-OF-corpus queries ungated (fabrication risk).
# Fix: decompose the long query into sentence/clause parts and refuse ONLY if NO
# part clears the normal short-query floor. Each part is short, so the validated
# 0.40 floor applies — no new threshold to calibrate. Gate-only: generation still
# uses the full-query retrieval. Deterministic; fail-open on any error.
#
# Additional `_relevance_gate` tiers this introduces: "decomposed_pass" (a sub-issue
# is in corpus → answer), "decomposed_all_below" (every part below floor → refuse),
# "exempt_long_error" (decomposition failed → fail-open).
LONGQUERY_GATE_ENABLED = os.environ.get("AVOKAI_LONGQUERY_GATE_ENABLED", "true").lower() in ("true", "1", "yes")
LONGQUERY_MAX_PARTS = int(os.environ.get("AVOKAI_LONGQUERY_MAX_PARTS", "6"))

_SENTENCE_SPLIT_RE = re.compile(r"[.!?;\n]+")


def _split_query_parts(query: str) -> list[str]:
    """Split a long query into substantive sentence/clause parts for the G2
    decomposition gate. Trivial fragments (greetings, "faleminderit") are dropped
    by requiring ≥12 chars and ≥2 content tokens (bm25 tokenizer).

    Future upgrade: an LLM (DeepSeek-Flash) semantic decomposition would split by
    legal *issue* rather than punctuation; sentence-split is the deterministic v1.
    """
    from app.ai.bm25_rescore import tokenize

    parts: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(query or ""):
        p = raw.strip()
        if len(p) < 12:
            continue
        if len(tokenize(p)) < 2:
            continue
        parts.append(p)
    return parts


def _long_query_gate(query: str, namespace: str) -> tuple[bool, RelevanceTier, float | None]:
    """G2 decomposition gate for long queries. Returns `(gated, tier, top_score)`
    mirroring `_relevance_gate`. Refuses only when NO substantive part is in-corpus.
    Gate-only — the caller still generates from the full-query retrieval when this
    passes. Fail-open: any error → exempt (never refuse a real kazus on a glitch).
    """
    parts = _split_query_parts(query)[:LONGQUERY_MAX_PARTS]
    # Only parts short enough for the validated floor to apply; a lone run-on
    # sentence (> the length cap) can't be scored reliably → keep the exemption.
    substantive = [p for p in parts if len(p) <= RELEVANCE_FLOOR_MAX_QUERY_LEN]
    if len(substantive) < 2:
        return (False, "exempt_long", None)

    best: float | None = None
    try:
        for part in substantive:
            part_sources = _semantic_retrieve(part, namespace)
            gated_p, _tier, top_p = _relevance_gate(part, part_sources)
            if top_p is not None:
                best = top_p if best is None else max(best, top_p)
            if not gated_p:
                # A sub-issue is answerable from the corpus → don't refuse.
                return (False, "decomposed_pass", top_p)
    except Exception:
        # Decomposition/retrieval glitch → fail open, never refuse a real kazus.
        return (False, "exempt_long_error", None)

    return (True, "decomposed_all_below", best)


# Multi-law bare-article backstop (audit finding G3). A bare-article query with
# no law ("çfarë thotë neni 47") that slips past the pre-retrieval clarify route
# lands here and retrieves the SAME article number from many unrelated laws; the
# LLM then synthesizes across all of them — a wrong-law fabrication. If the exact
# queried article number appears in ≥N distinct laws among the top sources, refuse
# with the "which law?" clarify instead of generating. Conservative + tunable; every
# trigger is traced so the threshold can be tightened from real data.
MULTILAW_GATE_ENABLED = os.environ.get("AVOKAI_MULTILAW_GATE_ENABLED", "true").lower() in ("true", "1", "yes")
MULTILAW_MIN_LAWS = int(os.environ.get("AVOKAI_MULTILAW_MIN_LAWS", "3"))


def _multilaw_ambiguous(query: str, sources: list[dict[str, Any]]) -> tuple[bool, int]:
    """Return `(gated, n_distinct_laws)` for the G3 backstop.

    Fires only when the query references an article by number but NO law of its
    own, and the retrieved sources spread that exact article number across
    ≥ MULTILAW_MIN_LAWS distinct laws — i.e. the answer would be cross-law
    synthesis of "Neni N" from unrelated laws.
    """
    if not MULTILAW_GATE_ENABLED or not sources:
        return (False, 0)
    # A query that names its own law isn't ambiguous (also exempts the
    # citation_miss_fallback path, whose query carries a real citation).
    if parse_citation(query) is not None:
        return (False, 0)
    m = ARTICLE_REF_PATTERN.search(query)
    if m is None:
        return (False, 0)
    n = m.group("n")
    laws: set[str] = set()
    for s in sources:
        meta = s.get("metadata") or s.get("document_metadata") or {}
        if str(meta.get("article_number") or "").strip() != n:
            continue
        law = (meta.get("law_number") or "").strip()
        if law:
            laws.add(law)
    return (len(laws) >= MULTILAW_MIN_LAWS, len(laws))


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
        "clarify": CLARIFY_RESPONSE,
        "clarify_missing_law": CLARIFY_MISSING_LAW_RESPONSE,
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
            "Sorry, this question falls outside the legislation in force in the "
            "Republic of Kosovo, which is the area I cover. I cannot help with "
            "foreign or comparative law, EU law, legal theory/philosophy, or "
            "non-legal topics. Please ask about Kosovo legislation."
        ),
        "clarify": (
            "The law number looks incomplete. Kosovo laws are identified by a full "
            "number in the form **NN/L-NNN** (e.g. 04/L-077); the number you gave "
            "(e.g. 04) is only the legislative term and applies to hundreds of laws. "
            "Please give me the full law number (e.g. *Article 47 of Law 04/L-077*) "
            "so I can find the exact article."
        ),
        "clarify_missing_law": (
            "You mentioned an article but not the law. Every law numbers its own "
            "articles, so I need the law to find the right one. Please specify the "
            "law number (e.g. *Article 37 of Law 03/L-212*)."
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


def _clarify_message_key(reason: str | None) -> str:
    """Pick the clarify wording for a pre-routed `clarify` intent from its reason.
    `article_without_law` → ask for the law; anything else → ask for the full number.
    Substring match: LLM-router reasons carry a prefix ("llm:article_without_law")."""
    return "clarify_missing_law" if "article_without_law" in (reason or "") else "clarify"


def _refusal_message(query: str, language: str, trace: dict[str, Any] | None = None) -> str:
    """Message for a semantic-path refusal (relevance floor / no sources).

    Post-retrieval safety net, two layers:
      1. If the query is a structurally incomplete legal reference (article with
         no law, or a bare/partial law number), steer with the matching clarify
         wording instead of the dead-end "not enough info". Loose form — retrieval
         has already failed, so this can never refuse an answerable query.
      2. Otherwise (vague / complex / long queries): keep the canonical refusal
         sentence VERBATIM (refusal-detection markers depend on it) and append an
         LLM-generated request for the specific missing information (legal field,
         law/article if known, concrete facts). Guard rails + discard rules live
         in app.ai.clarifier; on any failure the plain refusal ships unchanged.

    `trace` (the route_trace dict) is read for the gate tier and gains a
    `refusal_clarify` observability block when the clarifier runs. Blocking
    (one Flash call) — the stream path must call this off the event loop."""
    kind = incomplete_reference(query, strict=False)
    if kind is not None:
        return _localized_message(_clarify_message_key(kind), language)

    base = _localized_message("insufficient_context", language)
    tier = (trace or {}).get("relevance_floor_tier")
    parts = (
        _split_query_parts(query)[:LONGQUERY_MAX_PARTS]
        if tier == "decomposed_all_below"
        else None
    )
    t0 = time.time()
    extra = generate_clarify_request(
        query, _normalize_response_language(language), gate_tier=tier, parts=parts
    )
    if trace is not None:
        trace["refusal_clarify"] = {
            "used": extra is not None,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    return f"{base}\n\n{extra}" if extra else base


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
    return (
        f"I found no record of Law {info.law_number} being repealed in my abolishment "
        f"registry. This usually means the law is still in force, but I recommend "
        f"verifying it in the Official Gazette."
    )


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


# NOTE: the multi-law bare-article ambiguity backstop (G3) is IMPLEMENTED — see
# `_multilaw_ambiguous()` and its use in `answer()` / `answer_stream()`. This function
# stays retrieval-only and makes no gating decisions. (The G3 threshold still wants
# tuning against the LIVE index to avoid refusing legitimate topical queries.)
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
    context = derive_context(conversation_history)
    # Regex-first routing; a V4-Flash intent call ONLY on regex fall-through
    # (paraphrased greetings, dialect status queries, foreign-law refusals the
    # patterns miss). See router_llm.classify_with_fallback for the benchmark.
    decision, router_llm_trace = classify_with_fallback(query, context=context)
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
    if router_llm_trace is not None:
        trace["router_llm"] = router_llm_trace

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
    if decision.intent == "clarify":
        return AnswerResult(
            query=query,
            intent="clarify",
            answer=_localized_message(_clarify_message_key(decision.reason), lang),
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

    # Anti-fabrication gate: on the semantic path, if the best source is below
    # the relevance floor, refuse rather than generate from irrelevant chunks.
    # Layered (rerank → dense → fail-closed) so the gate is never silently off
    # when the reranker is absent (G1); the tier is traced for observability.
    relevance_gated = False
    if decision.intent == "semantic_question":
        if len(query or "") > RELEVANCE_FLOOR_MAX_QUERY_LEN and LONGQUERY_GATE_ENABLED:
            # G2: long queries are gated by decomposition instead of the blanket
            # length exemption (a long out-of-corpus query must not slip ungated).
            relevance_gated, floor_tier, floor_top = _long_query_gate(query, ns)
        else:
            relevance_gated, floor_tier, floor_top = _relevance_gate(query, sources)
        trace["relevance_floor_tier"] = floor_tier
        if floor_top is not None:
            trace["relevance_top_score"] = round(floor_top, 4)
        if relevance_gated:
            trace["relevance_floor_triggered"] = True
            # The retrieved chunks scored below the floor — they're irrelevant, so
            # don't surface them in the UI (or the abolishment scan). The answer is
            # a refusal; showing the rejected sources just looks broken.
            sources = []

    # Multi-law bare-article backstop (G3): if a bare-article query retrieved the
    # same article number across many unrelated laws, refuse-with-clarify rather
    # than let the LLM synthesize across them.
    multilaw_gated = False
    if decision.intent == "semantic_question" and not relevance_gated:
        multilaw_gated, n_laws = _multilaw_ambiguous(query, sources)
        if multilaw_gated:
            trace["multilaw_ambiguous"] = True
            trace["multilaw_distinct_laws"] = n_laws
            sources = []
            decision = RoutingDecision("clarify", None, "multilaw_ambiguous")

    # Keep route_trace's intent/reason in sync with the FINAL decision — it may have
    # been reassigned above (citation_miss_fallback → semantic_question, or the G3
    # multi-law gate → clarify), and AnswerResult.intent uses the final decision. The
    # citation_miss / multilaw_ambiguous trace flags still record the original routing.
    trace["intent"] = decision.intent
    trace["reason"] = decision.reason

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
    elif relevance_gated:
        # Best source below the relevance floor → refuse rather than fabricate.
        # If the query is an incomplete reference, steer instead of dead-ending;
        # otherwise append a targeted ask-for-more-information (clarifier).
        final_answer = _refusal_message(query, lang, trace)
    elif multilaw_gated:
        # Bare-article query matched the same article across many laws → ask which.
        final_answer = _localized_message("clarify_missing_law", lang)
    elif not use_llm:
        # Retrieval-only mode (used by retrieval eval). Stub out generation.
        final_answer = "[retrieval-only mode; no LLM answer generated]"
    elif not sources:
        final_answer = _refusal_message(query, lang, trace)
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
            result = complete(messages, temperature=0.1, max_tokens=MAX_ANSWER_TOKENS)
            final_answer = result.text
            if not final_answer.strip():
                # No visible content (reasoning exhausted the budget) → never
                # return an empty answer (G5 parity with the streaming path).
                final_answer = _localized_message("generation_error", lang)
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
            "relevance_floor": trace.get("relevance_floor_triggered", False),
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
    context = derive_context(conversation_history)
    # Regex-first routing; a V4-Flash intent call ONLY on regex fall-through.
    # Sync (blocking) call with an internal timeout — offload like retrieval so
    # the event loop (and the SSE heartbeat) keeps running.
    decision, router_llm_trace = await asyncio.to_thread(
        classify_with_fallback, query, context
    )
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
    if router_llm_trace is not None:
        trace["router_llm"] = router_llm_trace
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

    if decision.intent == "clarify":
        elapsed = int((time.time() - t0) * 1000)
        yield (
            "done",
            {
                "answer": _localized_message(_clarify_message_key(decision.reason), lang),
                "sources": [],
                "citations": [],
                "citation_summary": {},
                "abolishment_warnings": [],
                "intent": "clarify",
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

    # Layered anti-fabrication gate (rerank → dense → fail-closed), never
    # silently off when the reranker is absent (G1); tier traced for observability.
    relevance_gated = False
    if decision.intent == "semantic_question":
        if len(query or "") > RELEVANCE_FLOOR_MAX_QUERY_LEN and LONGQUERY_GATE_ENABLED:
            # G2: decomposition gate for long queries (does sync embed/Pinecone/
            # rerank, so offload like _semantic_retrieve).
            relevance_gated, floor_tier, floor_top = await asyncio.to_thread(
                _long_query_gate, query, ns
            )
        else:
            relevance_gated, floor_tier, floor_top = _relevance_gate(query, sources)
        trace["relevance_floor_tier"] = floor_tier
        if floor_top is not None:
            trace["relevance_top_score"] = round(floor_top, 4)
        if relevance_gated:
            trace["relevance_floor_triggered"] = True
            # The retrieved chunks scored below the floor — they're irrelevant, so
            # don't surface them in the UI (or the abolishment scan). The answer is
            # a refusal; showing the rejected sources just looks broken.
            sources = []

    # Multi-law bare-article backstop (G3) — must run before the `sources` emit
    # below so ambiguous cross-law sources are never surfaced.
    multilaw_gated = False
    if decision.intent == "semantic_question" and not relevance_gated:
        multilaw_gated, n_laws = _multilaw_ambiguous(query, sources)
        if multilaw_gated:
            trace["multilaw_ambiguous"] = True
            trace["multilaw_distinct_laws"] = n_laws
            sources = []
            decision = RoutingDecision("clarify", None, "multilaw_ambiguous")

    # Keep the final `done` route_trace consistent with the FINAL decision (it may have
    # been reassigned by citation_miss_fallback or the G3 multi-law gate). The earlier
    # `route` event intentionally kept the initial classify snapshot.
    trace["intent"] = decision.intent
    trace["reason"] = decision.reason

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
    elif relevance_gated:
        # Best source below the relevance floor → refuse rather than fabricate.
        # If the query is an incomplete reference, steer instead of dead-ending;
        # otherwise append a targeted ask-for-more-information (clarifier).
        # Blocking Flash call inside → off the event loop, like retrieval.
        final_answer = await asyncio.to_thread(_refusal_message, query, lang, trace)
        yield ("delta", {"text": final_answer})
    elif multilaw_gated:
        # Bare-article query matched the same article across many laws → ask which.
        final_answer = _localized_message("clarify_missing_law", lang)
        yield ("delta", {"text": final_answer})
    elif not use_llm:
        final_answer = "[retrieval-only mode; no LLM answer generated]"
        yield ("delta", {"text": final_answer})
    elif not sources:
        final_answer = await asyncio.to_thread(_refusal_message, query, lang, trace)
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
            async for event_type, payload in acomplete_stream(
                messages, temperature=0.1, max_tokens=MAX_ANSWER_TOKENS
            ):
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
            if not final_answer.strip():
                # No visible content (e.g. reasoning exhausted the token budget).
                # Never send an empty answer — the client would fall back to the
                # raw, UNVALIDATED accumulated deltas (G5). Emit a clear message.
                final_answer = _localized_message("generation_error", lang)
                yield ("delta", {"text": final_answer})
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
            "relevance_floor": trace.get("relevance_floor_triggered", False),
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
