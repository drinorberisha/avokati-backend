"""
Article-aware chunker for Kosovo legal text (v2 ingestion).

Designed to address the schema and structural problems found in the live
`default` namespace:
  - article_number metadata is null on every chunk (forces content-regex
    workarounds for direct citation lookup)
  - chunking is per-character window so chunks span article boundaries
  - 24% of chunks are sub-50-char fragments

The v2 chunker emits one chunk per legal article (`Neni N`), with proper
`law_number` and `article_number` metadata, the article title where
detectable, and the chapter context (`Kreu`) where present.

Designed to run against the cleanly-extracted text PyMuPDF produces from
the gazette PDFs — does NOT have to handle the `/uni0451`-style corruption
from the legacy PyPDF2 path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional


# ----- patterns ---------------------------------------------------------

# Article header. The PDFs use either:
#   "Neni 1 \nLënda\n\n<body>"
#   "Neni 1\nLënda\n<body>"
# Some laws use "Neni 1.\nTitle" with a trailing period. Some have no title
# (the next line is body).
#
# Capture (number, optional title-line). The title line is the immediate
# next non-empty line *only if it's short* (≤ 80 chars) and doesn't look
# like prose (no period in the middle, no ", " etc).
# Article headers across the corpus appear in three shapes:
#   (a) "Neni 1"            (line-end, classical)
#   (b) "Neni 1\nLënda"     (number alone, title on next line)
#   (c) "Neni 1 - Programi" / "Neni 1.  Title"  (number + inline title)
#
# We accept all three. The optional inline-title group is captured but
# title detection stays in `_detect_title` for consistency — it tries
# the inline form first, then falls back to the next-line heuristic.
ARTICLE_BOUNDARY = re.compile(
    r"(?m)^\s*Neni\s+(?P<num>\d{1,3})\s*"
    r"(?:[.\-–—:]\s*(?P<inline_title>[^\n]{1,80}?))?\s*$",
    re.IGNORECASE,
)

CHAPTER_BOUNDARY = re.compile(
    r"(?m)^\s*Kreu\s+([IVXLCDM]+|\d+)\b\s*(?P<title>[^\n]*)$",
    re.IGNORECASE,
)


@dataclass
class LegalChunk:
    """One chunk of legal text — typically one Neni."""

    law_number: str            # canonical "04/L-079"
    article_number: str        # "1", "2", "121"
    article_title: Optional[str]   # "Lënda" / "Qëllimi" / None
    chapter_number: Optional[str]
    chapter_title: Optional[str]
    chunk_type: str            # "article" | "preamble"
    content: str               # full article text including header
    chunk_id: str              # idempotent: "{law_safe}_art{N}"

    def to_pinecone_metadata(self, max_content_chars: int = 32_000) -> dict:
        """Serialize to a Pinecone-safe metadata dict.

        Pinecone caps metadata at 40KB total per record. Truncate content
        defensively at 32KB and flag the truncation so downstream code can
        fall back to disk if needed.
        """
        truncated = len(self.content) > max_content_chars
        content = self.content[:max_content_chars] if truncated else self.content
        return {
            "law_number": self.law_number,
            "article_number": self.article_number,
            "article_title": self.article_title or "",
            "chapter_number": self.chapter_number or "",
            "chapter_title": self.chapter_title or "",
            "chunk_type": self.chunk_type,
            "chunk_id": self.chunk_id,
            "content": content,
            "content_truncated": truncated,
        }


def _safe_law_number(law: str) -> str:
    """Canonical form for use in chunk IDs (no slashes, no spaces)."""
    return re.sub(r"[^\w-]", "_", law)


def _strip_header(text: str) -> tuple[str, dict]:
    """Pull off the gazette header (everything before the first Kreu/Neni).

    Returns (body, header_metadata). The header carries the Ligji Nr. and
    the publication info, which we don't currently use beyond the law number
    we already know.
    """
    first_neni = ARTICLE_BOUNDARY.search(text)
    first_kreu = CHAPTER_BOUNDARY.search(text)
    boundary = None
    for m in (first_kreu, first_neni):
        if m:
            boundary = m.start() if boundary is None else min(boundary, m.start())
    if boundary is None:
        return text, {}
    return text[boundary:], {"preamble": text[:boundary]}


def _detect_title(after_header: str) -> Optional[str]:
    """Pick a short non-empty line as the article title, if it looks like one."""
    lines = [ln.strip() for ln in after_header.split("\n")]
    for ln in lines[:3]:  # title is always within first few lines after header
        if not ln:
            continue
        if 3 <= len(ln) <= 80 and "." not in ln[:-1] and not ln[0].isdigit():
            # Avoid grabbing body-line "1. Shprehjet..." as the title
            if not re.match(r"^[\dIVX]+[.\s]", ln):
                return ln
        return None
    return None


# text-embedding-3-large input limit is 8192 tokens. Albanian legal text
# averages ~1.7-2.5 chars/token (lots of accented chars + long compounds),
# so we cap at 12K chars for ~5K tokens worst-case with comfortable
# safety margin. Articles longer than this are split into sub-chunks at
# paragraph boundaries.
MAX_CHARS_PER_EMBEDDING = 12_000


def _split_long_article(text: str, max_chars: int = MAX_CHARS_PER_EMBEDDING) -> list[str]:
    """Split an oversized article at paragraph boundaries to fit the embedding limit."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    paragraphs = re.split(r"\n\s*\n", text)
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        plen = len(para) + 2
        if current_len + plen > max_chars and current:
            parts.append("\n\n".join(current))
            current = [para]
            current_len = plen
        else:
            current.append(para)
            current_len += plen
    if current:
        parts.append("\n\n".join(current))
    # Hard fallback if a single paragraph exceeds the limit
    final: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
        else:
            for i in range(0, len(p), max_chars):
                final.append(p[i:i + max_chars])
    return final


def chunk_law(law_number: str, full_text: str) -> List[LegalChunk]:
    """Split a single law's full text into per-article chunks."""
    body, _ = _strip_header(full_text)

    # Map every chapter boundary to its position
    chapters = list(CHAPTER_BOUNDARY.finditer(body))
    chapter_lookup: list[tuple[int, str, str]] = [
        (m.start(), m.group(1), (m.group("title") or "").strip())
        for m in chapters
    ]

    def _chapter_for(pos: int) -> tuple[Optional[str], Optional[str]]:
        chap_num = chap_title = None
        for cstart, cnum, ctitle in chapter_lookup:
            if cstart <= pos:
                chap_num, chap_title = cnum, ctitle or None
            else:
                break
        return chap_num, chap_title

    matches = list(ARTICLE_BOUNDARY.finditer(body))
    if not matches:
        return []

    safe = _safe_law_number(law_number)
    chunks: list[LegalChunk] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        article_text = body[start:end].strip()
        if len(article_text) < 50:
            continue
        article_number = m.group("num")
        # Prefer the inline title if the header had one (`Neni 1 - Title`);
        # otherwise sniff the line after the header.
        inline_title = (m.groupdict().get("inline_title") or "").strip()
        if inline_title and 3 <= len(inline_title) <= 80:
            title = inline_title
        else:
            after_header = body[m.end():end]
            title = _detect_title(after_header)

        chap_num, chap_title = _chapter_for(start)
        # Split if the article exceeds the embedding-input limit; almost
        # always returns a single-element list.
        parts = _split_long_article(article_text)
        for sub_idx, part in enumerate(parts):
            sub_suffix = "" if len(parts) == 1 else f"_p{sub_idx}"
            chunks.append(
                LegalChunk(
                    law_number=law_number,
                    article_number=article_number,
                    article_title=title,
                    chapter_number=chap_num,
                    chapter_title=chap_title,
                    chunk_type="article" if len(parts) == 1 else "article_part",
                    content=part,
                    chunk_id=f"{safe}_art{article_number}{sub_suffix}",
                )
            )
    return chunks


__all__ = ["LegalChunk", "chunk_law"]
