"""
Intent router for AvokAI queries.

Decides which retrieval+generation path a query should take BEFORE we burn
embedding tokens or LLM calls on it. Routes:

  - `greeting`             → canned Albanian greeting, no retrieval, no LLM
  - `status_lookup`        → abolishment registry (no Pinecone)
  - `citation_lookup`      → metadata-filter retrieval against Pinecone
  - `semantic_question`    → dense + BM25 rescore retrieval, then LLM
  - `clarify`              → ask for the full law number, no retrieval, no LLM
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

from app.ai.citation import parse_citation, Citation, ARTICLE_REF_PATTERN
from app.ai.abolishment import is_status_query
from app.ai.conversation import ConversationContext, resolve_followup
from app.ai.text_norm import fold as _text_fold


Intent = Literal[
    "greeting",
    "status_lookup",
    "citation_lookup",
    "semantic_question",
    "clarify",
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


# Incomplete law reference: the user named "ligj(i/it) NN" but NN is only the
# legislative-term prefix (e.g. "04"), not a full "04/L-NNN" number. This only
# matters AFTER parse_citation() has returned None — at that point any "ligj +
# bare 1-2 digit number" is a truncated citation, not a real one. Routing it to
# `clarify` asks for the full number instead of silently semantic-searching a
# structural phrase that has nothing to match (the failure the user hit with
# "neni 47 i ligjit 04").
#
# Guards against false positives:
#   - `(?!\s*[-/_]?\s*[Ll])` — don't fire if the number actually starts a
#     "NN/L-NNN" form (those parse as a real citation upstream anyway).
#   - `(?!\d)` — the number must be a standalone 1-2 digits, so 4-digit years
#     ("ligji 2020") and longer numbers don't trip it.
# Uses diacritic-safe handling (Albanian "ligj" declensions: ligji/ligjit/...).
_INCOMPLETE_LAW_RE = re.compile(
    r"ligj\w*\s+(?:(?:nr|num[ëe]r\w*)\.?\s*)?\d{1,2}(?!\s*[-/_]?\s*[Ll])(?!\d)",
    re.IGNORECASE,
)

# Partial law number: "ligji 04/L" — the user started the "NN/L-NNN" form but
# stopped before the law number (no digits after the "L"). parse_citation needs
# digits after the L, so it returns None; treat this dangling form as incomplete.
_PARTIAL_LAW_RE = re.compile(
    r"ligj\w*\s+(?:(?:nr|num[ëe]r\w*)\.?\s*)?\d{1,2}\s*[-/_]\s*[Ll](?!\s*-?\s*\d)",
    re.IGNORECASE,
)


# Article reference with no law: "neni 37", "çfarë thotë neni 5?", "më trego
# nenin 12". These are structurally incomplete — an article number is meaningless
# without a law (every law has a "Neni 37"), so semantic search either refuses OR,
# worse, answers about a RANDOM law's article 37 (a fabrication). We detect the
# "citation-dominant" shape: an article ref remains once trivial filler words are
# removed, with NO real content words left. Any topical noun/verb ("pushimet
# vjetore") blocks it → that query stays on the semantic path.
_ARTICLE_TAIL_RE = re.compile(
    r"(?:paragraf\w*|pik[ëeat]*)\s*\.?\s*\d{1,3}", re.IGNORECASE
)
_WORD_RE = re.compile(r"[A-Za-zëçËÇ]+")


def _fold(token: str) -> str:
    """Fold a token for filler matching (see `text_norm.fold`): lowercase + drop
    Albanian diacritics (ë→e, ç→c), so diacritic-optional typing ("çfarë"/"cfare"/
    "çfare") collapses to one entry. We deliberately do NOT fold q→c — `q` is a real
    letter (qytetit, qeveria); the informal q-spellings of question words are listed
    explicitly in the filler set instead, so no real word is mis-folded."""
    return _text_fold(token)


# Wrapper words that surround a bare article reference. Stored FOLDED (ASCII, no
# diacritics). This is intentionally broad: the article-only verdict is blocked by
# any *topical noun* left in the residue, and topical nouns are never in this set —
# so listing question words, request/generic legal verbs, pronouns and particles
# here can't create false positives, but DOES make the detector robust to informal
# Kosovo spellings (qfare/qka/qysh/shka) and diacritic-free input.
_FILLER_WORDS = frozenset({
    # question words (incl. informal Kosovo forms: q- for ç-, sh- for ç')
    "cfare", "cfar", "cka", "ckfare", "qfare", "qfar", "qka", "qysh", "shka",
    "si", "kush", "cili", "cila", "cilat", "cilit", "kur", "ku", "sa", "pse", "perse",
    # request verbs ("tell/show/explain me")
    "trego", "tregom", "tregome", "tregoni", "jep", "jepme", "jepni", "shpjego",
    "shpjegoje", "sqaro", "spjego", "analizo", "interpreto",
    # generic "says / regulates / defines / addresses" verbs (never a topic)
    "thote", "thot", "thone", "thon", "flet", "flitet", "permban", "percakton",
    "percaktohet", "rregullon", "rregullohet", "trajton", "parashikon", "ndalon",
    "lejon", "specifikon",
    # pronouns / demonstratives
    "ky", "kjo", "ai", "ajo", "kete", "keto", "ketij", "kesaj", "atij", "asaj",
    "ate", "njejtin",
    # function words / particles
    "i", "e", "te", "me", "mua", "na", "ma", "nje", "dhe", "per", "mbi", "rreth",
    "eshte", "esht", "a", "apo", "po", "do", "ka", "kane", "kan",
    # deepening
    "shume", "teper", "pak", "gjate", "hollesi", "detaje",
    # English wrappers (EN queries hit the same router)
    "what", "does", "do", "say", "says", "tell", "show", "me", "explain", "of",
    "the", "article", "is", "are", "regulate", "define", "state", "provide", "about",
})


def _is_article_only(query: str) -> bool:
    """True if the query is essentially just an article reference (no law, no
    real topical content). High precision: any content word left over → False."""
    if not ARTICLE_REF_PATTERN.search(query):
        return False
    stripped = ARTICLE_REF_PATTERN.sub(" ", query)
    stripped = _ARTICLE_TAIL_RE.sub(" ", stripped)
    content = [t for t in _WORD_RE.findall(stripped) if _fold(t) not in _FILLER_WORDS]
    return not content


# Law-word declensions + "number" words that wrap a bare law citation. Folded.
_LAW_WRAPPER_WORDS = frozenset({"ligj", "ligji", "ligjit", "ligjin", "ligjet", "nr", "numer", "numri", "numrin", "law"})


def _is_law_only_completion(query: str, citation: Citation) -> bool:
    """True when the query is JUST a law citation with nothing else — the shape a
    user types when answering a clarify that asked for the full law number
    ("ligji 4 l 077"). High precision: any topical/status content word → False,
    so "a është në fuqi ligji 04/L-077" (status) and "çka thotë ligji 04/L-077
    për pagat" (topical) never count as completions."""
    if citation.article_number is not None or citation.by_name:
        return False
    stripped = query.replace(citation.raw_law, " ", 1)
    content = [
        t
        for t in _WORD_RE.findall(stripped)
        if _fold(t) not in _FILLER_WORDS and _fold(t) not in _LAW_WRAPPER_WORDS
    ]
    return not content


IncompleteKind = Literal["bare_law_prefix", "article_without_law"]


def incomplete_reference(query: str, *, strict: bool = True) -> IncompleteKind | None:
    """Classify a *structurally incomplete* legal reference, or return None.

    Only meaningful once parse_citation() has found no complete/named citation.

      - "bare_law_prefix"     → law cited by term-prefix only ("ligji 04", "ligji 04/L")
      - "article_without_law" → an article with no resolvable law ("neni 37")

    `strict` (default) is used for PRE-retrieval routing: the article case requires
    the query to be citation-dominant (`_is_article_only`) so a real topical question
    is never hijacked. `strict=False` is the loose form used by the pipeline's
    post-retrieval refusal-message net, where retrieval has ALREADY failed, so any
    lingering article reference just picks a better refusal wording (zero risk).
    """
    if not query or parse_citation(query) is not None:
        return None
    if _INCOMPLETE_LAW_RE.search(query) or _PARTIAL_LAW_RE.search(query):
        return "bare_law_prefix"
    if _is_article_only(query) if strict else bool(ARTICLE_REF_PATTERN.search(query)):
        return "article_without_law"
    return None


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


def classify(query: str, context: ConversationContext | None = None) -> RoutingDecision:
    """Decide the route. Cheap; no I/O.

    `context` (optional) carries the conversation's current focus so anaphoric /
    deepening follow-ups ("Paragrafi 1 i këtij neni", "neni 38", "shpjego më shumë")
    inherit the last `(law, article)` and route as a normal citation/status lookup
    instead of dead-ending in semantic search. Default None = stateless behavior.
    """
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
    if citation is not None:
        if is_status_query(query):
            return RoutingDecision("status_lookup", citation, "citation+status_keyword")
        # Clarify-completion merge: the previous USER turn referenced an article
        # ("neni 47 i ligjit 4 l77" → clarify asked for the full number) and this
        # turn is JUST the law ("ligji 4 l 077") — inherit that article so the
        # completion answers the original question. One-turn window
        # (last_user_article) + citation-dominance guard keep a brand-new
        # "ligji 08/L-035" question from inheriting stale articles.
        if (
            context is not None
            and context.last_user_article is not None
            and _is_law_only_completion(query, citation)
        ):
            merged = Citation(
                law_number=citation.law_number,
                article_number=context.last_user_article,
                raw_law=citation.raw_law,
                by_name=citation.by_name,
            )
            return RoutingDecision("citation_lookup", merged, "law_completion_inherited_article")
        # A law named-only (e.g. "ligji i punës") with no article and no status keyword
        # must NOT force a citation_lookup — that returns only the law's opening articles,
        # wrong for a topical sub-question. Route it as a normal lookup only when it
        # carries an article, or when it's an explicit NN/L number (unchanged behavior).
        if citation.article_number is not None or not citation.by_name:
            return RoutingDecision("citation_lookup", citation, "citation_parsed")

    # No citation of its own. If this is an anaphoric/deepening follow-up in an
    # ongoing conversation, inherit the focus (law, article) and route it like a
    # normal lookup — before incomplete_reference, so "neni 38" / "Paragrafi 1 i
    # këtij neni" inherit instead of clarifying or dead-ending in semantic search.
    if context is not None:
        inherited = resolve_followup(query, context)
        if inherited is not None:
            if is_status_query(query):
                return RoutingDecision("status_lookup", inherited, "followup_inherited_status")
            return RoutingDecision("citation_lookup", inherited, "followup_inherited_citation")

    # No complete citation parsed. If the user nonetheless made a structurally
    # incomplete reference — a bare law prefix ("ligji 04") or an article with no
    # law ("neni 37") — ask them to complete it instead of falling through to
    # semantic search (which either refuses or answers about the wrong law).
    kind = incomplete_reference(query)
    if kind is not None:
        return RoutingDecision("clarify", None, kind)

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

CLARIFY_RESPONSE = (
    "Numri i ligjit duket i paplotë. Ligjet e Kosovës identifikohen me numrin e "
    "plotë në formën **NN/L-NNN** (p.sh. 04/L-077); numri që dhatë (p.sh. 04) "
    "tregon vetëm legjislaturën dhe i përket qindra ligjeve. Ju lutem më jepni "
    "numrin e plotë të ligjit (p.sh. *Neni 47 i Ligjit 04/L-077*) që të mund ta "
    "gjej nenin e saktë."
)

# For an article referenced with no law ("neni 37"). Every ligj ka një Neni 37,
# so we can't know which one — ask for the law.
CLARIFY_MISSING_LAW_RESPONSE = (
    "E përmendët një nen, por jo ligjin. Çdo ligj ka numërimin e vet të neneve, "
    "prandaj më duhet edhe ligji për ta gjetur nenin e saktë. Ju lutem specifikoni "
    "numrin e ligjit (p.sh. *Neni 37 i Ligjit 03/L-212*)."
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
    "incomplete_reference",
    "GREETING_RESPONSE",
    "OUT_OF_SCOPE_RESPONSE",
    "CLARIFY_RESPONSE",
    "CLARIFY_MISSING_LAW_RESPONSE",
]
