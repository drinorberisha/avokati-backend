"""
Citation parser + filter-based retrieval for Albanian legal queries.

Handles the class of queries that pure semantic search fails on (Phase 0
baseline: 0/12 direct_citation, 0/8 status). Approach:

1. Parse `(law_number, article_number)` from natural-language query.
2. Look up chunks in Pinecone using `law_number` metadata filter (trying
   the small set of name variants observed in the live index, e.g. with
   and without `NR.` prefix).
3. If an article number was given, post-filter the chunk content for
   `Neni N` markers — the live index stores article structure inside the
   text (`## Neni 12`), not as a separate metadata field.

Designed so it can be used both from the backend retrieval service and
from the standalone eval harness (no heavy imports).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

# ----- patterns ----------------------------------------------------------

# Albanian law numbers come in a few shapes:
#   2002/1, 2004/21        (early UNMIK-era)
#   02/L-10, 04/L-079      (post-2008 standard)
#   05/L -011              (the index has a stray space in some entries)
#   KUV-08/L-032-KOD       (Kuvendi codes)
#
# Article references:
#   Neni 5, neni 5, NENI 5
#   Article numbers are 1–3 digits.

LAW_NUMBER_PATTERNS = [
    # Most-specific first: Kuvendi-style "KUV-NN/L-NNN-KOD"
    re.compile(r"\b(?P<law>KUV-\d{1,2}/L-\d{1,4}-[A-Z]+)\b", re.IGNORECASE),
    # Standard "NN/L-NNN" (with optional space inside the L)
    re.compile(r"\b(?P<law>\d{1,2}\s*/\s*[Ll]\s*-\s*\d{1,4}[A-Za-z0-9-]*)\b"),
    # UNMIK-era "YYYY/N"
    re.compile(r"\b(?P<law>(?:19|20)\d{2}\s*/\s*\d{1,3})\b"),
]

# Match Albanian "neni" with any case ending: neni, nenit, nenin, nenet, nenve, nenës, etc.
# Also tolerates an optional period: "Nenit 5." in answer text.
ARTICLE_REF_PATTERN = re.compile(
    r"\bnen(?:i|it|in|et|ve|ës|eve|i|e)?\s*\.?\s*(?P<n>\d{1,3})\b",
    re.IGNORECASE,
)

# Article header inside chunk content. The cleaner extractor uses Markdown:
#   "## Neni 12", "# Neni 10", sometimes "Neni 12<br>", with optional period
ARTICLE_HEADER_PATTERN = re.compile(
    r"(?:^|\n)\s*#{0,3}\s*Neni\s+(?P<n>\d{1,3})\b",
    re.IGNORECASE,
)


# ----- public API --------------------------------------------------------

@dataclass(frozen=True)
class Citation:
    """A parsed (law_number, article_number) pair extracted from a query."""

    law_number: str          # canonical form (uppercase, no whitespace, e.g. "04/L-079")
    article_number: str | None  # decimal string, e.g. "5", or None
    raw_law: str             # the substring that matched in the user's query


def canonicalize_law_number(raw: str) -> str:
    """Normalize a law number to its canonical form: uppercase, no whitespace."""
    return re.sub(r"\s+", "", raw).upper()


def parse_citation(query: str) -> Citation | None:
    """Extract a `(law_number, article_number)` pair from a query, if present.

    Returns None when no law number is found. Returns a Citation with
    `article_number=None` when only a law number is mentioned (e.g. status
    queries like "A është aktiv Ligji 04/L-226?").
    """
    if not query:
        return None
    law_match = None
    for pat in LAW_NUMBER_PATTERNS:
        m = pat.search(query)
        if m:
            law_match = m
            break
    if not law_match:
        return None

    raw_law = law_match.group("law")
    canonical = canonicalize_law_number(raw_law)

    art_match = ARTICLE_REF_PATTERN.search(query)
    article = art_match.group("n") if art_match else None

    return Citation(law_number=canonical, article_number=article, raw_law=raw_law)


def law_number_variants(law_number: str) -> list[str]:
    """Yield the small set of metadata-name variants observed in the live index.

    The index is inconsistent — some entries store `NR.03/L-094`, others store
    `03/L-094`. Querying must try both. Variants are returned in priority order;
    the caller should stop at the first one that yields hits.
    """
    canonical = canonicalize_law_number(law_number)
    variants = [
        canonical,
        f"NR.{canonical}",
        f"Nr.{canonical}",
        canonical.lower(),
        # Some indexed laws have a stray space, e.g. "05/L -011"
        canonical.replace("/L-", "/L -"),
    ]
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def chunk_starts_article(content: str, article_number: str) -> bool:
    """True if a chunk's content begins with (or near-begins with) the given Neni."""
    if not content:
        return False
    head = content[:400]
    for m in ARTICLE_HEADER_PATTERN.finditer(head):
        if m.group("n") == article_number:
            return True
    return False


def chunk_mentions_article(content: str, article_number: str) -> bool:
    """Looser: True if the chunk mentions the article anywhere as a header."""
    if not content:
        return False
    for m in ARTICLE_HEADER_PATTERN.finditer(content):
        if m.group("n") == article_number:
            return True
    return False


# ----- Pinecone-backed lookup --------------------------------------------

def lookup_by_citation(
    citation: Citation,
    pinecone_index: Any,
    namespace: str,
    dummy_vector: list[float],
    top_k: int = 10,
) -> dict[str, Any]:
    """Retrieve chunks for a parsed citation using only metadata filtering.

    Strategy:
      1. Try each `law_number` variant. Stop at the first one with hits.
      2. If `article_number` is set, prefer chunks where content begins
         with `## Neni N`. Fall back to chunks that mention it.
      3. If nothing found at all, return an empty list — better to say
         "law not indexed" than to return a wrong law (the failure mode
         the user already saw with "ligji 05/L-065" → returned 05/L-085).

    `dummy_vector` is a placeholder embedding; Pinecone's `query()` requires
    a vector even when filtering. We pass a constant zero-ish embedding —
    rank order isn't meaningful (we re-rank below) but the matches list is.

    Returns:
        {
            "matched_law_variant": str | None,
            "found_law": bool,
            "article_match_quality": "exact_start" | "mentions" | "none",
            "matches": list[dict],   # each: {id, score, metadata}
        }
    """
    matched_variant: str | None = None
    raw_matches: list[Any] = []

    for variant in law_number_variants(citation.law_number):
        res = pinecone_index.query(
            namespace=namespace,
            vector=dummy_vector,
            top_k=100,  # pull a generous superset; we'll re-rank locally
            include_metadata=True,
            filter={"law_number": variant},
        )
        if res.matches:
            matched_variant = variant
            raw_matches = list(res.matches)
            break

    if not raw_matches:
        return {
            "matched_law_variant": None,
            "found_law": False,
            "article_match_quality": "none",
            "matches": [],
        }

    # Format matches uniformly
    formatted = []
    for m in raw_matches:
        meta = dict(m.metadata or {})
        formatted.append({
            "id": m.id,
            "score": float(m.score) if m.score is not None else 0.0,
            "metadata": meta,
            "content": meta.get("content") or meta.get("text") or "",
        })

    # If no article number, return the law's chunks ordered by chunk_id when possible
    if citation.article_number is None:
        formatted.sort(key=lambda c: _chunk_sort_key(c["id"]))
        return {
            "matched_law_variant": matched_variant,
            "found_law": True,
            "article_match_quality": "none",  # no article requested
            "matches": formatted[:top_k],
        }

    # Article requested — prefer chunks whose content STARTS with that article
    starts: list[dict] = []
    mentions: list[dict] = []
    for c in formatted:
        if chunk_starts_article(c["content"], citation.article_number):
            starts.append(c)
        elif chunk_mentions_article(c["content"], citation.article_number):
            mentions.append(c)

    if starts:
        quality = "exact_start"
        # The asked-for article comes first, then immediate neighbors as
        # supporting context (legal interpretation depends on adjacent
        # articles), then any chunks that just mention it. Neighbors are
        # the chunks whose article_number metadata is N±1, falling back to
        # a chunk_id-adjacency heuristic when metadata is null (v1 schema).
        primary = starts[0]
        neighbors = _find_neighbor_chunks(formatted, primary, citation.article_number)
        # De-dup by id, preserve order: primary, neighbors, other-starts, mentions
        seen_ids: set[str] = {primary["id"]}
        ranked = [primary]
        for c in neighbors + starts[1:] + mentions:
            cid = c.get("id") or ""
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            ranked.append(c)
    elif mentions:
        quality = "mentions"
        ranked = mentions
    else:
        quality = "none"
        ranked = []  # don't pollute results with the rest of the law

    return {
        "matched_law_variant": matched_variant,
        "found_law": True,
        "article_match_quality": quality,
        "matches": ranked[:top_k],
    }


def _find_neighbor_chunks(
    pool: list[dict],
    primary: dict,
    article_number: str,
) -> list[dict]:
    """Pick the chunks that look like Article N-1 and N+1 of the same law.

    Strategy:
      1. If `metadata.article_number` is populated (v2 schema), find chunks
         where it's `int(N)-1` or `int(N)+1`.
      2. Otherwise (v1 schema where article_number is null), use `chunk_id`
         numeric adjacency: a chunk whose ID is `..._chunk_{i±1}` to the
         primary's `..._chunk_{i}`.

    Returns up to 2 chunks (one before, one after).
    """
    try:
        n = int(article_number)
    except ValueError:
        return []
    by_art: dict[int, dict] = {}
    by_chunkidx: dict[int, dict] = {}
    primary_chunk_idx = _chunk_sort_key(primary.get("id") or "")
    for c in pool:
        meta = c.get("metadata") or {}
        a = meta.get("article_number")
        if a is not None:
            try:
                by_art[int(a)] = c
            except (TypeError, ValueError):
                pass
        idx = _chunk_sort_key(c.get("id") or "")
        if idx != 10**9:
            by_chunkidx[idx] = c

    out: list[dict] = []
    # Prefer metadata-based neighbors (v2 schema)
    if n - 1 in by_art:
        out.append(by_art[n - 1])
    elif primary_chunk_idx != 10**9 and (primary_chunk_idx - 1) in by_chunkidx:
        out.append(by_chunkidx[primary_chunk_idx - 1])
    if n + 1 in by_art:
        out.append(by_art[n + 1])
    elif primary_chunk_idx != 10**9 and (primary_chunk_idx + 1) in by_chunkidx:
        out.append(by_chunkidx[primary_chunk_idx + 1])
    # Filter out the primary itself in case adjacency happens to match
    primary_id = primary.get("id")
    return [c for c in out if c.get("id") != primary_id]


# ----- internals ---------------------------------------------------------

_CHUNK_NUM_RE = re.compile(r"chunk[_-](\d+)")


def _chunk_sort_key(chunk_id: str) -> int:
    """Pull the numeric suffix from chunk IDs like `doc-464.pdf_chunk_21`."""
    m = _CHUNK_NUM_RE.search(chunk_id or "")
    return int(m.group(1)) if m else 10**9


__all__ = [
    "Citation",
    "canonicalize_law_number",
    "parse_citation",
    "law_number_variants",
    "chunk_starts_article",
    "chunk_mentions_article",
    "lookup_by_citation",
]
