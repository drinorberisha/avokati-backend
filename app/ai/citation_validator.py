"""
Validate citations in an LLM-generated answer against the retrieved sources.

LLMs hallucinate citations. The Albanian QA prompt mandates the format
`[Neni N, Ligji X/L-Y]` for every claim — we extract those, then check
whether each one actually appears in the metadata of the retrieved chunks
that fed the prompt. Unverified citations get a `⚠️ unverified` marker so
they're visually flagged in the UI rather than silently trusted.

Relationship to the parser in `citation.py`:
  - That parser pulls citations out of *user queries* (intent: "this is a
    citation lookup, do metadata filtering").
  - This validator pulls citations out of *LLM answers* (intent: "did the
    model cite something it actually saw, or did it make it up?").

We deliberately use a different, looser regex here because LLM output
formatting drifts from query phrasing (e.g. the model might write
`[Ligji 04/L-079, Neni 5]` or `(Neni 5 i Ligjit 04/L-079)`). We accept
any of these forms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Match citations in any of the natural shapes the LLM tends to produce:
#   [Neni 5, Ligji 02/L-10]
#   [Neni 5 i Ligjit 02/L-10]
#   (Neni 5, Ligji 02/L-10)
#   Neni 5 i Ligjit 02/L-10
#   [Ligji 02/L-10]   (no article — bare law citation)
#
# Capture groups: 'num' = article number (optional), 'law' = law number
_LAW_PATTERN = (
    r"(?:KUV-)?\d{1,2}\s*/\s*[Ll]\s*-\s*\d{1,4}[A-Za-z0-9-]*"
    r"|(?:19|20)\d{2}\s*/\s*\d{1,3}"
)

# Albanian declensions on root "nen" (article): neni, nenit, nenin, nenet,
# nenve, nenës, neneve. Use non-capturing alternations rather than character
# classes so multi-letter suffixes work.
_NEN = r"nen(?:i|it|in|et|ve|ës|eve|e)?"

# Both "Ligji" (law) and "Kodi" (code) prefix legal text in Albanian. Codes
# are a separate document class in Kosovo law: Kodi Penal (criminal),
# Kodi Doganor (customs), Kodi Civil, etc. Each has the same `XX/L-NNN`
# numbering as a Ligji but is referenced differently by Albanian-fluent
# LLMs. Missing this used to flag legitimate citations as hallucinated —
# the LLM would say "Kodit Nr. 08/L-247" and the validator wouldn't
# recognize the form even though we DID retrieve KUV-08/L-247-KOD from
# Pinecone.
_LIGJ = r"(?:ligj|kod)(?:i|it|in|et|ve|ës|eve|e|ave|ore|ash)?"

# LLMs often emit "Ligjit Nr. 08/L-247" or "Kodit Nr. 08/L-247" — the
# "Nr." is optional and varies in case. Tolerate it between the prefix
# and the number, plus optional whitespace and an optional period.
_NR_OPT = r"(?:\s*nr\.?\s*)?"

_CITATION_RE = re.compile(
    rf"""(?ix)
    \b
    (?:
        # "Neni N ... Ligji X/L-Y" ordering
        {_NEN}\.?\s*(?P<num1>\d{{1,3}})
        [^.\n\[\]\(\)]{{0,40}}?
        {_LIGJ}\.?{_NR_OPT}\s*(?P<law1>{_LAW_PATTERN})
      |
        # "Ligji X/L-Y ... Neni N" ordering
        {_LIGJ}\.?{_NR_OPT}\s*(?P<law2>{_LAW_PATTERN})
        [^.\n\[\]\(\)]{{0,40}}?
        {_NEN}\.?\s*(?P<num2>\d{{1,3}})
      |
        # Bare law (no article)
        {_LIGJ}\.?{_NR_OPT}\s*(?P<law3>{_LAW_PATTERN})
    )
    """
)


@dataclass(frozen=True)
class ExtractedCitation:
    span: tuple[int, int]
    raw: str
    law_number: str          # canonical (uppercase, no whitespace)
    article_number: str | None


@dataclass(frozen=True)
class ValidatedCitation:
    citation: ExtractedCitation
    verified: bool
    matched_source_id: str | None


def _canon_law(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def _law_inner(s: str) -> str:
    """Strip Kosovo code wrappers from a law number for fuzzy comparison.

    `KUV-08/L-247-KOD` and `08/L-247` are the same law indexed under two
    different naming conventions — Kuvendi codes (KUV-...-KOD) wrap the
    inner number. The validator needs to recognize either form citing the
    other. Returns the inner part if a wrapper is present, else the input.
    """
    canon = _canon_law(s)
    # KUV-08/L-247-KOD → 08/L-247
    m = re.match(r"^KUV-(\d{1,2}/L-\d{1,4})(?:-[A-Z]+)?$", canon)
    if m:
        return m.group(1)
    return canon


def extract_citations(text: str) -> list[ExtractedCitation]:
    """Pull every citation out of an answer."""
    out: list[ExtractedCitation] = []
    for m in _CITATION_RE.finditer(text or ""):
        law = m.group("law1") or m.group("law2") or m.group("law3")
        num = m.group("num1") or m.group("num2")
        if not law:
            continue
        out.append(
            ExtractedCitation(
                span=(m.start(), m.end()),
                raw=m.group(0),
                law_number=_canon_law(law),
                article_number=num,
            )
        )
    return out


def validate_against_sources(
    citations: list[ExtractedCitation],
    sources: list[dict[str, Any]],
    *,
    authoritative_laws: set[str] | None = None,
) -> list[ValidatedCitation]:
    """Mark each citation verified iff a retrieved source covers it.

    Match rules:
      - The cited law number must appear as `metadata.law_number` of at
        least one source (after canonicalization).
      - If the citation includes an article number, at least one source
        for that law must have `metadata.article_number == that_number`,
        OR the source's content must start with `## Neni N` for that N
        (covers v1-namespace chunks where article_number metadata is
        null but the article header is in the body).
      - **Special case**: if a law number is in `authoritative_laws`, a
        bare-law citation to it counts as verified even when no chunk for
        that law was retrieved. This covers abolishment warnings injected
        into the prompt context — the model legitimately cites the
        abolisher law (e.g. "shfuqizuar nga Ligji 08/L-205") even though
        the abolisher's chunks weren't part of the retrieval set.
    """
    # Index sources by both their canonical form and their inner-number
    # form so a citation in either shape can find them.
    by_law: dict[str, list[dict[str, Any]]] = {}
    for src in sources:
        meta = src.get("metadata") or src.get("document_metadata") or {}
        law = _canon_law(meta.get("law_number") or "")
        if not law:
            continue
        by_law.setdefault(law, []).append(src)
        inner = _law_inner(law)
        if inner != law:
            by_law.setdefault(inner, []).append(src)

    # Authoritative laws (from abolishment registry) — same dual-form trick
    auth = set(authoritative_laws or set())
    for a in list(auth):
        auth.add(_law_inner(a))

    results: list[ValidatedCitation] = []
    for c in citations:
        # Try the cited law number directly, then its inner-form (so a
        # citation to "08/L-247" matches an indexed "KUV-08/L-247-KOD" and
        # vice versa).
        candidates = by_law.get(c.law_number, []) or by_law.get(_law_inner(c.law_number), [])
        if not candidates:
            # Last-chance: authoritative-laws fallback for bare-law citations
            if c.article_number is None and (
                c.law_number in auth or _law_inner(c.law_number) in auth
            ):
                results.append(ValidatedCitation(c, verified=True, matched_source_id=None))
                continue
            results.append(ValidatedCitation(c, verified=False, matched_source_id=None))
            continue
        if c.article_number is None:
            # Bare law citation — any source under this law verifies it
            sid = (candidates[0].get("metadata") or candidates[0].get("document_metadata") or {}).get("chunk_id")
            results.append(ValidatedCitation(c, verified=True, matched_source_id=sid))
            continue
        target = c.article_number
        match: dict[str, Any] | None = None
        for src in candidates:
            meta = src.get("metadata") or src.get("document_metadata") or {}
            if str(meta.get("article_number") or "") == target:
                match = src
                break
            content = src.get("content") or meta.get("content") or ""
            if re.search(rf"(?:^|\n)\s*#{{0,3}}\s*Neni\s+{re.escape(target)}\b", content[:500], re.IGNORECASE):
                match = src
                break
        if match is not None:
            sid = (match.get("metadata") or match.get("document_metadata") or {}).get("chunk_id")
            results.append(ValidatedCitation(c, verified=True, matched_source_id=sid))
        else:
            results.append(ValidatedCitation(c, verified=False, matched_source_id=None))
    return results


def annotate_unverified(text: str, validated: list[ValidatedCitation]) -> str:
    """Return the answer text with `⚠️ unverified` appended after each
    citation that we couldn't ground in the retrieved sources.

    Walks the text right-to-left so earlier insertions don't shift later
    spans.
    """
    if not validated:
        return text
    out = text
    for v in sorted(validated, key=lambda x: x.citation.span[0], reverse=True):
        if v.verified:
            continue
        end = v.citation.span[1]
        out = out[:end] + " ⚠️ i paverifikuar" + out[end:]
    return out


def summarize(validated: list[ValidatedCitation]) -> dict[str, Any]:
    """Stats for logging / dashboards."""
    n = len(validated)
    verified = sum(1 for v in validated if v.verified)
    return {
        "citations_total": n,
        "citations_verified": verified,
        "citations_unverified": n - verified,
        "verified_rate": round(verified / n, 3) if n else 1.0,
    }


__all__ = [
    "ExtractedCitation",
    "ValidatedCitation",
    "extract_citations",
    "validate_against_sources",
    "annotate_unverified",
    "summarize",
]
