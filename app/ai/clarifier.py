"""
Refusal clarifier for AvokAI: turn dead-end refusals into information requests.

When the anti-fabrication gates refuse ("Nuk kam informacion të mjaftueshëm..."),
the user today gets no guidance on what WOULD let us answer. For structurally
incomplete citations the pipeline already steers ("which law?"); this module
covers the remaining refusals — vague, complex, or long queries — with ONE
V4-Flash call that generates a short, targeted request for the missing
information (legal field, specific law/article if known, concrete facts),
appended after the canonical refusal sentence.

Anti-fabrication contract (this text goes verbatim into a legal product, so
the guards are deterministic, not just prompt instructions):

  1. The clarifier may ONLY ask questions. The prompt forbids answering,
     citing, or naming any law/article — and the OUTPUT IS SCANNED: if it
     contains a law-number shape, a resolvable named law, or an article
     reference (`parse_citation` / ARTICLE_REF_PATTERN — the same detectors
     the pipeline trusts), the whole text is DISCARDED. A clarifier that
     hints at the wrong law is worse than the dead-end.
  2. Length-bounded: over-long output is discarded, not truncated (a cut-off
     sentence in a legal product looks broken).
  3. Fail-safe: any API error, timeout, or empty/garbled output → None, and
     the caller keeps today's exact refusal text.
  4. The canonical refusal sentence is NEVER altered — the clarifier only
     supplies text to APPEND, so refusal-detection markers (abstention eval,
     any frontend logic) keep matching.

Latency is a non-issue here: this path only runs when generation was skipped
(the gate refused), so the turn is otherwise near-instant; ~1-1.5s of Flash
is still far below a normal generation turn. Cost ~$0.00002/refusal.
"""

from __future__ import annotations

import os
import re

from app.ai.citation import ARTICLE_REF_PATTERN, parse_citation

# Kill-switch, consistent with the pipeline's gate knobs and the LLM router.
REFUSAL_CLARIFY_ENABLED = os.environ.get(
    "AVOKAI_REFUSAL_CLARIFY_ENABLED", "true"
).lower() in ("true", "1", "yes")
REFUSAL_CLARIFY_TIMEOUT_S = float(os.environ.get("AVOKAI_REFUSAL_CLARIFY_TIMEOUT_S", "4.0"))

# Discard-don't-truncate bound. Generous: 2 sentences + 3 bullet questions in
# Albanian fit well under this; anything longer means the model rambled.
_MAX_CHARS = 900

# Belt-and-braces on top of parse_citation: any "NN/L-NNN"-ish shape or a
# 4-digit-year law form in the output → discard. parse_citation would catch
# most of these, but a malformed number it fails to parse must ALSO not ship.
_LAW_SHAPE_SCAN = re.compile(r"\d{1,4}\s*[-/_]\s*[Ll]\s*-?\s*\d|\b(?:19|20)\d{2}\s*/\s*\d{1,3}")


# Static system prompt (prompt-cache friendly). The task is deliberately
# narrow: ask for missing information, never supply legal content.
_SYSTEM_PROMPT = """\
You write the follow-up for AvokAI, a Kosovo-law assistant, in the situation \
where the system could NOT find relevant Kosovo legislation for the user's \
query and has already told them so. Your ONLY job is to ask the user for the \
missing information that would make the query answerable.

STRICT RULES — violating any of these makes the output unusable:
- ONLY ask questions / request information. NEVER answer the query, never \
explain what the law says, never give legal advice or opinions.
- NEVER mention, suggest, or cite any specific law, code, law number, or \
article number. Not even as an example.
- Ask for things like: the legal field (punësim, kontrata, familje, pronë, \
penale, tregti...), the specific law or article the user may have in mind \
(without suggesting one), the concrete facts of the situation (who, what, \
when, what document/contract exists), or a simpler rephrasing.
- If the query has several parts, target your questions at those parts.
- Output: one short lead-in sentence, then 2-3 bullet questions ("- ..."). \
Maximum ~80 words. No greeting, no apology (the refusal text before yours \
already handled that), no closing line.
- Write in the language you are told (Albanian or English). Albanian should \
be simple and natural (Kosovo usage).\
"""


def _validate(text: str) -> str | None:
    """Deterministic output guards. Returns cleaned text or None (discard)."""
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > _MAX_CHARS:
        return None
    # Any resolvable citation (number or curated named law) → the model broke
    # the no-law rule → discard the whole thing.
    if parse_citation(cleaned) is not None:
        return None
    if ARTICLE_REF_PATTERN.search(cleaned):
        return None
    if _LAW_SHAPE_SCAN.search(cleaned):
        return None
    return cleaned


def generate_clarify_request(
    query: str,
    language: str,
    *,
    gate_tier: str | None = None,
    parts: list[str] | None = None,
) -> str | None:
    """Generate the ask-for-more-information text to append to a refusal.

    Args:
        query: the user's original query (verbatim).
        language: "sq" or "en" — the response language of the turn.
        gate_tier: relevance-gate tier that refused (e.g. "rerank",
            "decomposed_all_below") — tells the model whether this was a
            vague short query or a long multi-part one.
        parts: for long-query (G2) refusals, the decomposed sentence parts,
            so the questions target the user's actual sub-issues.

    Returns the text to append, or None (caller keeps the plain refusal).
    Never raises.
    """
    if not REFUSAL_CLARIFY_ENABLED or not query or not query.strip():
        return None

    lang_name = "English" if language == "en" else "Albanian"
    user_lines = [f"Reply language: {lang_name}."]
    if gate_tier == "decomposed_all_below" and parts:
        user_lines.append(
            "The query is long and was analyzed in these parts (none matched "
            "Kosovo legislation):"
        )
        user_lines.extend(f"  {i + 1}. {p}" for i, p in enumerate(parts[:6]))
    user_lines.append(f"User query:\n{query.strip()}")

    try:
        from app.ai.llm import ChatMessage, complete

        result = complete(
            [
                ChatMessage("system", _SYSTEM_PROMPT),
                ChatMessage("user", "\n".join(user_lines)),
            ],
            fast=True,
            temperature=0.0,
            max_tokens=220,
            timeout=REFUSAL_CLARIFY_TIMEOUT_S,
            # V4 models reason by default; see router_llm — the deliberation
            # would eat the token budget and the latency.
            extra_body={"thinking": {"type": "disabled"}},
        )
        return _validate(result.text)
    except Exception:  # noqa: BLE001 — a refusal must never fail because of its garnish
        return None


__all__ = [
    "REFUSAL_CLARIFY_ENABLED",
    "REFUSAL_CLARIFY_TIMEOUT_S",
    "generate_clarify_request",
]
