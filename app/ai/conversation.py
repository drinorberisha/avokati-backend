"""
Conversation focus carry-forward for AvokAI follow-ups.

The router (`app.ai.router`) and retrieval are stateless — they classify a query
in isolation. That breaks context-dependent follow-ups: after

    user:      "Neni 37 i Ligjit 03/L-212"
    assistant: "...rregullon pushimin vjetor [Neni 37, par. 1, Ligji 03/L-212]..."
    user:      "A mund të thellohesh te Paragrafi 1 i këtij neni?"

the third turn has no citation of its own, routes to `semantic_question`, has
nothing to embed against, fails the relevance floor, and dead-ends — even though
`conversation_history` reaches the LLM prompt (the follow-up never gets there).

This module recovers the conversation's *focus* — the most recently discussed
`(law, article)` — from the history text (reusing `parse_citation`), and decides
whether the current query is an anaphoric/deepening follow-up that should INHERIT
that focus. The router then routes it as a normal `citation_lookup` / `status_lookup`.

Deterministic and cheap (regex only), matching the router's design. A learned /
LLM query-rewrite fallback for arbitrary phrasings is documented at the bottom for
when the regex layer starts missing real follow-ups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.ai.citation import (
    ARTICLE_REF_PATTERN,
    Citation,
    parse_citation,
)


@dataclass(frozen=True)
class ConversationContext:
    """The conversation's current focus, recovered from prior turns."""

    focus_law: str | None = None       # canonical law_number, e.g. "03/L-212"
    focus_article: str | None = None   # article number, e.g. "37"

    @property
    def has_focus(self) -> bool:
        return self.focus_law is not None


# Follow-up signals — the current query refers back to the focus rather than
# introducing a new subject. Diacritic-safe: Python's \b misbehaves on ë/ç, so we
# use explicit non-letter lookarounds around Albanian stems.
_NL = r"(?<![A-Za-zëçËÇ])"  # left "word" boundary that tolerates diacritics

# Anaphora: "këtij neni", "këtë ligj", "ky nen", "i njëjti ligj", "të njëjtin nen"...
_ANAPHORA_RE = re.compile(
    _NL + r"(?:k[ëe]tij|k[ëe]saj|atij|asaj|k[ëe]t[ëe]|at[ëe]|ky|kjo|ai|ajo|nj[ëe]jt\w*)\s+"
    r"(?:nen|ligj|paragraf|pik|dispozit|kod)\w*",
    re.IGNORECASE,
)
# Bare paragraph / point reference with no law of its own ("Paragrafi 1", "pika 3").
_PARA_REF_RE = re.compile(
    _NL + r"(?:paragraf\w*|pik[ëeat]*)\s*\.?\s*\d{1,3}", re.IGNORECASE
)
# Deepening / continuation requests ("më shumë", "thellohu", "shpjego", "vazhdo"...).
_DEEPEN_RE = re.compile(
    _NL + r"(?:"
    r"(?:m[ëe]|me)\s+(?:shum[ëe]|tep[ëe]r|gjat[ëe]|holl[ëe]si|detaje)"
    r"|thello\w*|zgjero\w*|elaboro\w*|shpjego\w*|sqaro\w*|vazhdo\w*|detaj\w*"
    r"|more|elaborate|explain\s+further|go\s+deeper|continue|expand"
    r")",
    re.IGNORECASE,
)
# NOTE: no bare "po/dhe/e ..." continuation-opener signal on purpose — those words
# are far too common to mark a follow-up without hijacking new questions. The real
# continuations ("po paragrafi 2?", "dhe neni 39?") are already caught by the
# paragraph-ref and bare-article branches, which carry their own specific anchor.


# Example citations inside a parenthetical "(p.sh. Neni 37 i Ligjit 03/L-212)"
# are NOT the conversation focus — they're illustrations. Our own canned clarify /
# greeting messages carry these (with real example law numbers), so scanning them
# for focus would seed a phantom law and hijack the next follow-up (a real bug:
# after a "which law?" clarify, "neni 5" inherited the example's 03/L-212). Strip
# these spans before deriving focus. Covers Albanian "p.sh." / "për shembull" and
# English "e.g.".
_EXAMPLE_SPAN_RE = re.compile(
    r"\(\s*(?:p\.?\s*sh\.?|p[ëe]r\s+shembull|e\.?\s*g\.?)[^)]*\)", re.IGNORECASE
)


def _content_of(turn: Any) -> str:
    """Pull the text out of a history turn (dict or object)."""
    if isinstance(turn, dict):
        return str(turn.get("content") or "")
    return str(getattr(turn, "content", "") or "")


def _focus_text(turn: Any) -> str:
    """Turn text with example-citation spans removed, for focus derivation."""
    return _EXAMPLE_SPAN_RE.sub(" ", _content_of(turn))


def derive_context(history: list[Any] | None) -> ConversationContext:
    """Recover the conversation's focus from prior turns.

    Walks most-recent-first. `focus_law` is the first turn that yields a
    `parse_citation` law_number; `focus_article` is the first turn with any
    article reference (decoupled, so the article still tracks if a later turn
    names it without restating the law).
    """
    if not history:
        return ConversationContext()

    focus_law: str | None = None
    focus_article: str | None = None
    for turn in reversed(history):
        text = _focus_text(turn)
        if not text:
            continue
        if focus_law is None:
            cit = parse_citation(text)
            if cit is not None:
                focus_law = cit.law_number
                if focus_article is None and cit.article_number is not None:
                    focus_article = cit.article_number
        if focus_article is None:
            m = ARTICLE_REF_PATTERN.search(text)
            if m:
                focus_article = m.group("n")
        if focus_law is not None and focus_article is not None:
            break
    return ConversationContext(focus_law=focus_law, focus_article=focus_article)


def _is_followup_signal(query: str) -> bool:
    return bool(
        _ANAPHORA_RE.search(query)
        or _PARA_REF_RE.search(query)
        or _DEEPEN_RE.search(query)
    )


def resolve_followup(query: str, ctx: ConversationContext | None) -> Citation | None:
    """Return an inherited Citation when `query` is a context-dependent follow-up.

    None means "not a follow-up (or nothing to inherit)" — let normal routing run.
    Rules (in order):
      1. Query has its own citation → None (it stands alone).
      2. No focus law in context → None.
      3. Query has a bare article ref (no law) → inherit the focus law with the
         query's NEW article ("neni 38" → same law, article 38).
      4. Query matches a follow-up signal → inherit focus law + focus article.
      5. Otherwise → None (a brand-new subject).
    """
    if not query or ctx is None or not ctx.has_focus:
        return None
    if parse_citation(query) is not None:
        return None

    art_match = ARTICLE_REF_PATTERN.search(query)
    if art_match is not None:
        # "neni 38" mid-conversation → article 38 of the SAME law.
        return Citation(
            law_number=ctx.focus_law,  # type: ignore[arg-type]
            article_number=art_match.group("n"),
            raw_law=ctx.focus_law or "",
        )

    if _is_followup_signal(query):
        return Citation(
            law_number=ctx.focus_law,  # type: ignore[arg-type]
            article_number=ctx.focus_article,
            raw_law=ctx.focus_law or "",
        )
    return None


# ---------------------------------------------------------------------------
# Future upgrade: when the regex layer starts missing real follow-ups (track via
# feedback on turns that refused right after a citation answer), add a cheap
# DeepSeek-V4-Flash "condense to standalone query" call as a fallback AFTER
# resolve_followup returns None:
#
#   standalone = llm.complete([CONDENSE_PROMPT, recent_history, query], fast=True)
#   decision   = classify(standalone)
#
# Cost ~$0.00007/turn; only pay it on unresolved anaphoric turns.
# ---------------------------------------------------------------------------


__all__ = [
    "ConversationContext",
    "derive_context",
    "resolve_followup",
]
