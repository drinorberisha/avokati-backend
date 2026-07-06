"""
Response schemas for `/legal-ai/ask-v2` — the post-rebuild AvokAI endpoint.

Designed so the frontend can render the answer + sidebar with NO additional
backend calls. Everything the UI needs (article metadata, badges,
verified-citation flags, abolishment status, source URLs) is in this one
response.

Kept in its own module rather than `legal_document.py` so a future cleanup
can delete the legacy schemas without touching this file.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# Bands the frontend renders as colored pills instead of "26%".
ScoreBand = Literal["strong", "moderate", "topical", "weak"]
Intent = Literal[
    "greeting",
    "out_of_scope",
    "clarify",
    "status_lookup",
    "citation_lookup",
    "semantic_question",
]


class SourceCard(BaseModel):
    """One retrieved chunk, enriched with everything the sidebar needs.

    Fields mirror the v2 chunk metadata (`v2_chunker.LegalChunk.to_pinecone_metadata`)
    plus per-law catalog enrichment (publication date, gazette URL, full title).
    """

    id: str = Field(..., description="Chunk ID, e.g. '02_L-10_art5'")
    law_number: str = Field(..., description="Canonical law number, e.g. '02/L-10' or 'KUV-08/L-247-KOD'")
    law_title: Optional[str] = Field(None, description="Full law title from catalog, e.g. 'LIGJI NR. 02/L-10 PËR PËRKUJDESJEN NDAJ KAFSHËVE'")
    article_number: Optional[str] = Field(None, description="Article number as a string, e.g. '5'")
    article_title: Optional[str] = Field(None, description="Article title, e.g. 'Kujdesi ndaj kafshëve'")
    chapter_number: Optional[str] = Field(None, description="Chapter (Kreu) number if present, e.g. 'II'")
    chapter_title: Optional[str] = Field(None, description="Chapter title if present")
    chunk_type: str = Field("article", description="One of: 'article', 'article_part', 'status_verdict', 'abolisher', 'abolished'")
    content: str = Field(..., description="Full chunk content. May be truncated for very long articles — see content_truncated.")
    content_truncated: bool = Field(False, description="True if the content shown is truncated relative to the source PDF")

    # Score + presentation
    score: float = Field(..., description="Raw retrieval score (cosine for semantic, 1.0 for citation/status hits)")
    score_band: ScoreBand = Field(..., description="Bucketed score for UI rendering")

    # Badges / state
    is_primary: bool = Field(False, description="True for the asked-for article in a citation_lookup; the LLM treats this as authoritative")
    is_cited: bool = Field(False, description="True if the LLM emitted a citation that resolves to this chunk")
    is_abolished: bool = Field(False, description="True if this chunk's parent law has a 'fully_abolished' or 'partially_abolished' relation in abolishment_relations.json")
    abolished_by: list[str] = Field(default_factory=list, description="Law numbers that abolish this one, if any")
    abolishment_type: Optional[str] = Field(None, description="'full' or 'partial' if abolished")

    # Catalog enrichment (best-effort; null when not in catalog)
    publication_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD when the law was published in the gazette")
    gazette_number: Optional[str] = Field(None, description="Gazette issue, e.g. '2019/2'")
    law_url: Optional[str] = Field(None, description="Direct link to the law on gzk.rks-gov.net")


class CitationRecord(BaseModel):
    """One `[Neni N, Ligji X/L-Y]` extracted from the LLM answer + verification status."""

    raw: str = Field(..., description="As emitted by the LLM, e.g. '[Neni 5, Ligji 02/L-10]' or 'Nenit 8 të Ligjit 02/L-10'")
    law_number: str = Field(..., description="Canonical law number parsed from the citation")
    article_number: Optional[str] = None
    verified: bool = Field(..., description="True iff the cited (law, article) is in retrieved sources OR the abolishment-authoritative set")
    matched_source_id: Optional[str] = Field(None, description="Chunk ID this citation resolves to, if any")


class LlmUsage(BaseModel):
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    usd_cost_estimate: float = 0.0
    finish_reason: Optional[str] = None


class ChatTurn(BaseModel):
    """One prior conversation turn.

    Role is restricted to user/assistant — a client-supplied `system` entry
    would be spliced between our system prompt and the question, i.e. a
    direct prompt-injection channel. Content is capped to bound token cost.
    """

    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=8000)


class AskV2Request(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="The user's question, in any language")
    use_llm: bool = Field(True, description="If False, skip generation and return retrieval only (used by eval / debug)")
    namespace: Optional[Literal["default_v2"]] = Field(None, description="Pinecone namespace; only the live v2 namespace is accepted. Defaults to env PINECONE_NAMESPACE_V2 (=default_v2)")
    response_language: Literal["sq", "en"] = Field(
        "sq",
        description="Language AvokAI must use for user-facing answers: sq=Albanian, en=English.",
    )
    conversation_history: Optional[list[ChatTurn]] = Field(
        None,
        max_length=12,
        description="Optional prior turns ({role: user|assistant, content}). Server uses the last 6 entries.",
    )
    session_id: Optional[str] = Field(
        None,
        description="Optional chat session UUID. If provided, the turn is persisted into chat_messages and the session's last_message_at is bumped. Server rejects if the session isn't owned by the caller.",
    )


class AskV2Response(BaseModel):
    """Single-call rich response for the new AvokAI sidebar.

    The frontend should be able to render the chat bubble AND the sidebar
    entirely from this object — no follow-up calls.
    """

    query: str
    answer: str = Field(..., description="Answer in the requested response language, with warning markers on any unverified citations")
    intent: Intent = Field(..., description="Which retrieval/generation path was used")
    sources: list[SourceCard] = Field(default_factory=list)
    citations: list[CitationRecord] = Field(default_factory=list)
    abolishment_warnings: list[str] = Field(
        default_factory=list,
        description="Albanian-language warnings emitted when retrieved sources reference abolished laws",
    )
    elapsed_ms: int = 0
    llm_usage: Optional[LlmUsage] = Field(None, description="Null when no LLM was called (greeting/status/oos paths)")
    route_trace: dict[str, Any] = Field(default_factory=dict, description="Diagnostic info for debugging — not for end-user display")


__all__ = [
    "AskV2Request",
    "AskV2Response",
    "ChatTurn",
    "SourceCard",
    "CitationRecord",
    "LlmUsage",
    "ScoreBand",
    "Intent",
]
