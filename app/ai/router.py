"""
Intent router for AvokAI queries.

Decides which retrieval+generation path a query should take BEFORE we burn
embedding tokens or LLM calls on it. Routes:

  - `greeting`             → canned Albanian greeting, no retrieval, no LLM
  - `status_lookup`        → abolishment registry (no Pinecone)
  - `citation_lookup`      → metadata-filter retrieval against Pinecone
  - `semantic_question`    → dense + BM25 rescore retrieval, then LLM
  - `out_of_scope`         → polite refusal, no retrieval

The first version is a pragmatic regex/keyword classifier. It costs nothing,
runs in microseconds, and handles the obvious cases. A learned classifier
upgrade (one DeepSeek-V4-Flash call) is documented at the bottom for when
edge cases start mattering — e.g. ambiguous mixed queries, jurisprudential
hypotheticals.

Why route at all instead of always falling through to semantic:
  - "Përshëndetje" wasted ~$0.0003 + 800ms on the live system before this.
  - Citation-only queries achieve 100% retrieval accuracy via metadata
    filtering; mixing them with semantic adds noise.
  - Status queries don't need vector retrieval at all — the answer is in
    `abolishment_relations.json`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.ai.citation import parse_citation, Citation
from app.ai.abolishment import is_status_query


Intent = Literal[
    "greeting",
    "status_lookup",
    "citation_lookup",
    "semantic_question",
    "out_of_scope",
]


# Albanian greetings + small-talk openers. Kept conservative — we'd rather
# route an ambiguous "tungjatjeta" to greeting than to semantic search.
_GREETING_RE = re.compile(
    r"^(?:\s*)(?:"
    r"përshëndetje|pershendetje|tungjatjeta|tung|mirëdita|mire dita|mirëmëngjes|"
    r"mirembrema|miremengjes|hello|hi|hey|"
    r"si\s+jeni|si\s+je|c'kemi|qkemi|ckemi|ç'kemi"
    r")\b\s*[!?\.\s]*$",
    re.IGNORECASE,
)


# Out-of-scope topics. These trigger a polite refusal so the LLM never
# generates non-legal advice. Add patterns here as they come up in real use.
#
# Note: avoid `\b` around Albanian-stem patterns — Python's word boundary
# misbehaves on diacritics (`ë`, `ç`). Use explicit non-letter lookaround
# or a stem match without boundaries.
_OUT_OF_SCOPE_PATTERNS = [
    # Cooking
    re.compile(r"(?<![A-Za-zëçËÇ])(recet|kuzhin|gatim)", re.IGNORECASE),
    # Personal/relationship
    re.compile(r"(?<![A-Za-zëçËÇ])(dashuri|romanc)", re.IGNORECASE),
    # Medical
    re.compile(r"(?<![A-Za-zëçËÇ])(mjekim|sëmundje|semundje|mjekësi|mjekesi)", re.IGNORECASE),
    # Cybersec / illegal
    re.compile(r"\b(hack|exploit|crack|breach)\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class RoutingDecision:
    intent: Intent
    citation: Citation | None
    reason: str  # human-readable why this intent was picked

    @property
    def needs_retrieval(self) -> bool:
        return self.intent in ("status_lookup", "citation_lookup", "semantic_question")

    @property
    def needs_llm(self) -> bool:
        # LLM is only needed for semantic_question. The other paths build
        # their answer deterministically from registry + retrieved chunks.
        return self.intent == "semantic_question"


def classify(query: str) -> RoutingDecision:
    """Decide the route. Cheap; no I/O."""
    if not query or not query.strip():
        return RoutingDecision("greeting", None, "empty_query")

    if _GREETING_RE.match(query):
        return RoutingDecision("greeting", None, "matched_greeting_pattern")

    for pat in _OUT_OF_SCOPE_PATTERNS:
        if pat.search(query):
            return RoutingDecision("out_of_scope", None, f"matched_oos:{pat.pattern!r}")

    citation = parse_citation(query)
    if citation is not None and is_status_query(query):
        return RoutingDecision("status_lookup", citation, "citation+status_keyword")
    if citation is not None:
        return RoutingDecision("citation_lookup", citation, "citation_parsed")

    return RoutingDecision("semantic_question", None, "default_fallthrough")


# Canned Albanian responses for the no-retrieval intents.
GREETING_RESPONSE = (
    "Përshëndetje! Unë jam AvokAI, asistent ligjor për legjislacionin e Kosovës. "
    "Si mund t'ju ndihmoj me një pyetje juridike sot? Mund të më pyesni për një "
    "nen specifik (p.sh. *Çfarë thotë Neni 5 i Ligjit 02/L-10?*), për statusin e "
    "një ligji, ose për dispozita ligjore për një temë."
)

OUT_OF_SCOPE_RESPONSE = (
    "Më vjen keq, por unë jam i specializuar vetëm në legjislacionin e Kosovës "
    "dhe nuk mund t'ju ndihmoj me pyetje jashtë kësaj fushe. Ju lutem më drejtoni "
    "një pyetje ligjore."
)


# ---------------------------------------------------------------------------
# Future upgrade: replace classify() with a DeepSeek-V4-Flash call when:
#   - we see >5% of queries take the wrong route (track via feedback)
#   - users ask multi-clause queries the regex layer can't handle
#
# Sketch:
#   def classify_llm(query: str) -> RoutingDecision:
#       resp = llm.complete(
#           [ChatMessage("system", FLASH_ROUTER_PROMPT_SQ),
#            ChatMessage("user", query)],
#           fast=True, temperature=0.0, max_tokens=20,
#       )
#       intent = resp.text.strip().lower()
#       ...
#
# Cost at V4-Flash: ~$0.00007 per query. Cache on (sha256(query), 24h).
# ---------------------------------------------------------------------------


__all__ = [
    "Intent",
    "RoutingDecision",
    "classify",
    "GREETING_RESPONSE",
    "OUT_OF_SCOPE_RESPONSE",
]
