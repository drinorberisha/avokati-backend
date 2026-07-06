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
#   Article numbers are 1–4 digits (the Law on Obligations runs to Neni 1059).

# Users (and the UI/dir names) often type the separator as `_` instead of `/`
# — "04_L-086" for "04/L-086". Accept both `[/_]`; canonicalize_law_number then
# normalizes `_`→`/` so the metadata filter matches the index's slash form.
LAW_NUMBER_PATTERNS = [
    # Most-specific first: Kuvendi-style "KUV-NN/L-NNN-KOD"
    re.compile(r"\b(?P<law>KUV\s*-?\s*\d{1,2}\s*[-/_]?\s*[Ll]\s*-?\s*\d{1,4}\s*-?\s*[A-Z]+)\b", re.IGNORECASE),
    # Standard "NN/L-NNN". Be liberal about how the user separates the parts:
    # the canonical "02/L-10" but also "02 L10", "02L10", "02/L10", "02-L-10",
    # "02 L 10" — any of `/ _ -` space or nothing between the pieces. The literal
    # "L" flanked by digits is the anchor; canonicalize_law_number rebuilds the
    # canonical "NN/L-NNN" form. (Users type this a dozen ways — see the tests.)
    re.compile(r"\b(?P<law>\d{1,2}\s*[-/_]?\s*[Ll]\s*-?\s*\d{1,4}[A-Za-z0-9-]*)\b"),
    # UNMIK-era "YYYY/N"
    re.compile(r"\b(?P<law>(?:19|20)\d{2}\s*[/_]\s*\d{1,3})\b"),
]

# Match Albanian "neni" with any case ending: neni, nenit, nenin, nenet, nenve, nenës, etc.
# Also tolerates an optional period: "Nenit 5." in answer text.
ARTICLE_REF_PATTERN = re.compile(
    r"\bnen(?:i|it|in|et|ve|ës|eve|i|e)?\s*\.?\s*(?P<n>\d{1,4})\b",
    re.IGNORECASE,
)

# Article header inside chunk content. The cleaner extractor uses Markdown:
#   "## Neni 12", "# Neni 10", sometimes "Neni 12<br>", with optional period
ARTICLE_HEADER_PATTERN = re.compile(
    r"(?:^|\n)\s*#{0,3}\s*Neni\s+(?P<n>\d{1,4})\b",
    re.IGNORECASE,
)

# Some primary sources are referenced by NAME or standard abbreviation, not an
# "NN/L-NNN" number. Each entry maps a diacritic-tolerant, declension-aware pattern
# to a VERIFIED, in-force canonical law_number (looked up in law_catalog.json —
# where multiple versions exist we take the current one, e.g. Criminal Code
# 06/L-074, not the repealed 04/L-082). A wrong number here is a fabrication, so
# this list is curated and conservative; extend it only with verified numbers.
#
# ORDER MATTERS: more specific patterns must come first. "Kodi i Procedurës Penale"
# is listed before "Kodi Penal"; the inheritance pattern excludes "kulturore" so it
# never grabs the Cultural Heritage law (02/L-88).
_NAMED_LAW_PATTERNS = [
    # Constitution — "Kushtetuta/Kushtetutës/…" but NOT "Kushtetuese" (Constitutional
    # Court; stem "kushtetu-ese" has no second 't').
    (re.compile(r"\bkushtetut\w*", re.IGNORECASE), "KUSHTETUTA"),
    # Criminal PROCEDURE code — must precede the Criminal Code below.
    (re.compile(r"\bkod\w*\s+(?:i\s+|t[ëe]\s+)?proced\w*\s+penal\w*|(?<![A-Za-z])KPPK?(?![A-Za-z])",
                re.IGNORECASE), "KUV-08/L-032-KOD"),
    # Civil / contested PROCEDURE (Procedura Kontestimore).
    (re.compile(r"\bproced\w*\s+kontestimor\w*|(?<![A-Za-z])LPK(?![A-Za-z])",
                re.IGNORECASE), "03/L-006"),
    # Criminal Code (Kodi Penal / KPRK).
    (re.compile(r"\bkod\w*\s+penal\w*|(?<![A-Za-z])KPRK(?![A-Za-z])",
                re.IGNORECASE), "KUV-06/L-074-KOD"),
    # Law on Obligations (Marrëdhëniet e Detyrimeve / LMD).
    (re.compile(r"\bmarr[ëe]dh[ëe]niet?\s+e\s+detyrimeve|lig\w*\s+(?:i\s+|p[ëe]r\s+)?detyrimeve|(?<![A-Za-z])LMD(?![A-Za-z])",
                re.IGNORECASE), "04/L-077"),
    # Labor Law (Ligji i Punës / për Punën).
    (re.compile(r"\blig\w*\s+(?:i\s+|t[ëe]\s+|p[ëe]r\s+)?pun[ëe]s?\b", re.IGNORECASE), "03/L-212"),
    # Family Law (Ligji për Familjen).
    (re.compile(r"\blig\w*\s+(?:i\s+|t[ëe]\s+|p[ëe]r\s+)?familj\w*", re.IGNORECASE), "2004/32"),
    # Inheritance Law (Ligji për Trashëgiminë) — NOT "trashëgiminë kulturore" (02/L-88).
    (re.compile(r"\blig\w*\s+(?:i\s+|t[ëe]\s+|p[ëe]r\s+)?trash[ëe]gimin?[ëe]?(?!\w*\s+kulturor)", re.IGNORECASE), "2004/26"),
]


# ----- public API --------------------------------------------------------

@dataclass(frozen=True)
class Citation:
    """A parsed (law_number, article_number) pair extracted from a query."""

    law_number: str          # canonical form (uppercase, no whitespace, e.g. "04/L-079")
    article_number: str | None  # decimal string, e.g. "5", or None
    raw_law: str             # the substring that matched in the user's query
    by_name: bool = False    # True when resolved from a law NAME/abbrev (not an NN/L number)


_LAW_SHAPE_RE = re.compile(r"(KUV-)?(\d{1,4})[-/]?L-?(\d{1,4})(-?[A-Z0-9]+)?")


def canonicalize_law_number(raw: str) -> str:
    """Normalize a law number to the canonical form stored in the index
    ("02/L-10", "KUV-08/L-247-KOD").

    Strips whitespace, uppercases, and — crucially — rebuilds the "NN/L-NNN"
    shape from however the user separated the parts, so "02 L10", "02L10",
    "02/L10", "02-L-10", "02_L-10" all collapse to "02/L-10". UNMIK-era
    "YYYY/N" numbers (no "L") pass through unchanged.
    """
    s = re.sub(r"\s+", "", raw).upper().replace("_", "/")
    m = _LAW_SHAPE_RE.fullmatch(s)
    if m:
        prefix, series, num, suffix = m.group(1) or "", m.group(2), m.group(3), m.group(4) or ""
        return f"{prefix}{series}/L-{num}{suffix}"
    return s


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

    art_match = ARTICLE_REF_PATTERN.search(query)
    article = art_match.group("n") if art_match else None

    if law_match:
        raw_law = law_match.group("law")
        return Citation(law_number=canonicalize_law_number(raw_law),
                        article_number=article, raw_law=raw_law)

    # Named-source fallback: primary sources cited by name, not an NN/L-NNN number.
    for pat, canonical in _NAMED_LAW_PATTERNS:
        nm = pat.search(query)
        if nm:
            return Citation(law_number=canonical, article_number=article,
                            raw_law=nm.group(0), by_name=True)
    return None


def _law_inner(canonical: str) -> str:
    """Strip a Kuvendi code wrapper: "KUV-08/L-032-KOD" → "08/L-032". Returns the
    input unchanged when there's no wrapper."""
    m = re.match(r"^KUV-(\d{1,2}/L-\d{1,4})(?:-[A-Z0-9]+)?$", canonical)
    return m.group(1) if m else canonical


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
    # Kuvendi code wrappers ("KUV-08/L-032-KOD") and their inner number ("08/L-032")
    # are the same law under two naming conventions — the index may store either, so
    # probe both. Add the inner form (and its NR. variant) when a wrapper is present.
    inner = _law_inner(canonical)
    if inner != canonical:
        variants.extend([inner, f"NR.{inner}", inner.lower()])
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
    """Retrieve chunks for a parsed citation using metadata filtering.

    Strategy:
      1. Find which `law_number` spelling the index uses (variant probe).
      2. If `article_number` is set, filter directly on the `article_number`
         metadata for {N-1, N, N+1} — exact, and crucially independent of
         how many articles the law has. (The old approach pulled a top_k=100
         sample of the law and content-scanned it, which silently missed the
         target on laws with >100 articles, e.g. 04/L-077's Neni 1059.)
         A content-scan fallback covers any chunk that lacks the metadata
         field (legacy v1 schema).
      3. If the law isn't found at all, return empty — better to say
         "law not indexed" than to return a wrong law (the failure mode
         the user already saw with "ligji 05/L-065" → returned 05/L-085).

    `dummy_vector` is a placeholder embedding; Pinecone's `query()` requires
    a vector even for filter-only queries. Score order is meaningless here —
    we order results by article number, not similarity.

    Returns:
        {
            "matched_law_variant": str | None,
            "found_law": bool,
            "article_match_quality": "exact_start" | "mentions" | "none",
            "matches": list[dict],   # each: {id, score, metadata, content}
        }
    """
    def _format(matches: Iterable[Any]) -> list[dict]:
        out: list[dict] = []
        for m in matches:
            meta = dict(m.metadata or {})
            out.append({
                "id": m.id,
                "score": float(m.score) if m.score is not None else 0.0,
                "metadata": meta,
                "content": meta.get("content") or meta.get("text") or "",
            })
        return out

    # 1. Identify the law_number spelling the index actually uses. A tiny
    #    probe (top_k=1, no metadata) is enough and cheap.
    matched_variant: str | None = None
    for variant in law_number_variants(citation.law_number):
        probe = pinecone_index.query(
            namespace=namespace,
            vector=dummy_vector,
            top_k=1,
            include_metadata=False,
            filter={"law_number": variant},
        )
        if probe.matches:
            matched_variant = variant
            break

    if matched_variant is None:
        return {
            "matched_law_variant": None,
            "found_law": False,
            "article_match_quality": "none",
            "matches": [],
        }

    # 2. No article requested — return the law's OPENING articles (purpose,
    #    scope, definitions), which is what "what does law X regulate" wants.
    #    Filter explicitly on the low article numbers so we get 1,2,3... and
    #    not an arbitrary dummy-vector sample of the law.
    if citation.article_number is None:
        wanted = [str(i) for i in range(1, max(top_k, 10) + 1)]
        res = pinecone_index.query(
            namespace=namespace,
            vector=dummy_vector,
            top_k=len(wanted) * 2,  # headroom for multi-part `_p` chunks
            include_metadata=True,
            filter={"law_number": matched_variant, "article_number": {"$in": wanted}},
        )
        formatted = _format(res.matches)
        formatted.sort(key=_article_sort_key)
        return {
            "matched_law_variant": matched_variant,
            "found_law": True,
            "article_match_quality": "none",  # no article requested
            "matches": formatted[:top_k],
        }

    # 3. Article requested — targeted metadata filter on {N-1, N, N+1}.
    art = citation.article_number
    try:
        n = int(art)
        wanted = [str(n - 1), str(n), str(n + 1)]
    except ValueError:
        wanted = [art]

    res = pinecone_index.query(
        namespace=namespace,
        vector=dummy_vector,
        top_k=50,  # the article + neighbors, plus any multi-part `_p` chunks
        include_metadata=True,
        filter={"law_number": matched_variant, "article_number": {"$in": wanted}},
    )
    formatted = _format(res.matches)
    exact = [
        c for c in formatted
        if str((c["metadata"] or {}).get("article_number", "")).strip() == art
    ]
    if exact:
        # Requested article first (parts in id order), then neighbors for
        # legal context — adjacent articles often qualify a provision.
        exact.sort(key=lambda c: c["id"])
        neighbors = [c for c in formatted if c not in exact]
        neighbors.sort(key=_article_sort_key)
        ranked = exact + neighbors
        return {
            "matched_law_variant": matched_variant,
            "found_law": True,
            "article_match_quality": "exact_start",
            "matches": ranked[:top_k],
        }

    # 4. Fallback for chunks without article_number metadata (legacy v1):
    #    pull a pool of the law and content-scan for the `Neni N` header.
    res = pinecone_index.query(
        namespace=namespace,
        vector=dummy_vector,
        top_k=100,
        include_metadata=True,
        filter={"law_number": matched_variant},
    )
    formatted = _format(res.matches)
    starts = [c for c in formatted if chunk_starts_article(c["content"], art)]
    mentions = [
        c for c in formatted
        if c not in starts and chunk_mentions_article(c["content"], art)
    ]

    if starts:
        primary = starts[0]
        neighbors = _find_neighbor_chunks(formatted, primary, art)
        seen_ids: set[str] = {primary["id"]}
        ranked = [primary]
        for c in neighbors + starts[1:] + mentions:
            cid = c.get("id") or ""
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            ranked.append(c)
        quality = "exact_start"
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


def _article_sort_key(c: dict) -> tuple[int, int]:
    """Order chunks by their article number (v2 metadata), falling back to
    the v1 chunk-id index. Chunks with neither sort last."""
    meta = c.get("metadata") or {}
    a = meta.get("article_number")
    try:
        return (0, int(a))
    except (TypeError, ValueError):
        return (1, _chunk_sort_key(c.get("id") or ""))


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
