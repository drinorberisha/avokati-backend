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
    r"si\s+jeni|si\s+je|a\s+je\s+mir[ëe]?|a\s+jeni\s+mir[ëe]?|qysh\s+je(ni)?|"
    r"si\s+po\s+(ja\s+)?kalon|c'kemi|qkemi|ckemi|ç'kemi|ckemi"
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


# Out-of-CORPUS legal questions: comparative/foreign law, EU law, legal
# philosophy, world-trivia. These ARE legal questions, but the answer is not in
# the Kosovo gazette corpus, so generating from whatever retrieves anyway risks
# a confidently-cited WRONG answer (the "konfederata" → trade-union-law failure).
# This is the precise, pre-retrieval half of the anti-fabrication gate (the
# post-retrieval half is the relevance floor in pipeline.py); the floor backstops
# whatever these patterns miss.
#
# Tuned for PRECISION: validated at 0 false positives across 83 in-corpus golden
# queries. We would rather miss an out-of-corpus query (the floor catches it) than
# refuse a legitimate Kosovo-law question. Use diacritic-safe lookarounds, not \b,
# around Albanian stems (Python word boundaries misbehave on ë/ç).
_OUT_OF_CORPUS_PATTERNS = [
    # United States / comparative constitutional law
    re.compile(r"\bSHBA\b|\bUSA\b|shtetet?\s+e\s+bashkuara", re.IGNORECASE),
    # Other foreign jurisdictions
    re.compile(r"(?<![A-Za-zëçËÇ])(franc[ëe]s|francez|belgjik|gjermani|britani|britanik|zvicr|amerikan)", re.IGNORECASE),
    # EU institutions
    re.compile(r"bashkimi\s+evropian|\bUE\b|\bBE\b|parlament\w*\s+evropian|banka\s+qendrore\s+evropiane|gjykat\w*\s+e\s+drejt[ëe]sis[ëe]\s+s[ëe]\s+(ue|be)|komision\w*\s+evropian|kart\w*\s+(themelore|sociale)\s+\w*(evropian|europian|e\s+be)", re.IGNORECASE),
    # EU treaties / integration history
    re.compile(r"traktat\w*\s+(i\s+)?(rom[ëe]s|mastrit|lisbon|nic[ëe]s|parisit|amsterdam|bruksel|kushtetues)|akti\s+unik\s+(evropian|europian)|plani\s+marshall|tregu\s+i\s+brend|ant[ëa]r[ëe]sim\w*\s+n[ëe]\s+bashk[ëe]si|trio\s+presidency|presidenc\w*\s+e\s+tresh", re.IGNORECASE),
    # Legal philosophy / theorists (specific declensions, to avoid e.g. "hartë"=map)
    re.compile(r"(?<![A-Za-zëçËÇ])(hartit|hobs|hobbes|kelzen|kelsen|akuin|benthamit|monteskje|rusoi)", re.IGNORECASE),
    # Comparative state forms
    re.compile(r"(?<![A-Za-zëçËÇ])konfederat", re.IGNORECASE),
    re.compile(r"shtet(e|et)\s+federativ|federaliz\w*", re.IGNORECASE),
    # World-comparative trivia ("...më e gjatë në botë")
    re.compile(r"n[ëe]\s+bot[ëe](?![A-Za-zëçËÇ])", re.IGNORECASE),
    # International covenants / ILO conventions by number
    re.compile(r"pakti\s+nd[ëe]rkomb[ëe]tar|konvent\w*\s+(numer|nr)\.?\s*\d|drejt[ëe]s?\s+nd[ëe]rkomb[ëe]tar\w*\s+t[ëe]\s+pun", re.IGNORECASE),
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

    # Out-of-corpus legal questions (comparative/EU/philosophy/foreign) →
    # refuse before retrieval rather than risk fabricating a Kosovo-law citation.
    for pat in _OUT_OF_CORPUS_PATTERNS:
        if pat.search(query):
            return RoutingDecision("out_of_scope", None, "matched_out_of_corpus")

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
    "Më vjen keq, por kjo pyetje del jashtë legjislacionit në fuqi të Republikës "
    "së Kosovës, që është fusha që mbuloj. Nuk mund të ndihmoj me të drejtën e huaj "
    "ose krahasuese, të drejtën e Bashkimit Evropian, teorinë/filozofinë juridike, "
    "apo tema joligjore. Ju lutem parashtroni një pyetje për legjislacionin e Kosovës."
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
