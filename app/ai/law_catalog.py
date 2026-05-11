"""
Per-law metadata enrichment (publication date, gazette URL, full title).

The v2 chunker only stores per-article metadata in Pinecone (law_number,
article_number, article_title, chapter_*). It does NOT carry the law's
publication date, gazette number, or the official URL on gzk.rks-gov.net.
Those live in `Scraping/data/law_results.json`, the byproduct of the
scraper.

This module loads that JSON once at startup and exposes a `lookup(law_number)`
that returns the per-law fields, keyed by canonical law_number (also tries
the inner-form mapping for KUV-...-KOD codes).

Why we don't just stuff this into Pinecone metadata: it would require
re-upserting all 42K vectors any time the catalog metadata changes
(new gazette URLs, corrections, etc.), and the catalog is small enough
to keep in memory.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
LAW_RESULTS_PATH = REPO_ROOT / "Scraping" / "data" / "law_results.json"


@dataclass(frozen=True)
class LawCatalogEntry:
    law_number: str          # canonical, uppercase, no spaces
    title: str | None
    publication_date_iso: str | None  # YYYY-MM-DD, parsed from DD.MM.YYYY
    gazette_number: str | None
    url: str | None


class LawCatalog:
    """In-memory map keyed by canonical law_number → LawCatalogEntry.

    Resilient to missing/old `law_results.json` — `lookup()` returns
    `None` rather than raising when the law isn't in the catalog.
    """

    _lock = threading.Lock()
    _instance: "LawCatalog | None" = None

    def __init__(self, path: Path = LAW_RESULTS_PATH) -> None:
        self.path = path
        self._by_number: dict[str, LawCatalogEntry] = {}
        self._load()

    @classmethod
    def get(cls) -> "LawCatalog":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        for row in rows:
            num = row.get("law_number")
            if not num:
                continue
            canonical = _canon(num)
            entry = LawCatalogEntry(
                law_number=canonical,
                title=row.get("title") or None,
                publication_date_iso=_parse_date(row.get("publication_date")),
                gazette_number=row.get("gazette_number") or None,
                url=row.get("url") or None,
            )
            self._by_number[canonical] = entry
            # Also map the inner-form for KUV-...-KOD wrappers so callers
            # can hit either form.
            inner = _law_inner(canonical)
            if inner != canonical and inner not in self._by_number:
                self._by_number[inner] = entry

    def lookup(self, law_number: str | None) -> Optional[LawCatalogEntry]:
        if not law_number:
            return None
        canonical = _canon(law_number)
        return self._by_number.get(canonical) or self._by_number.get(_law_inner(canonical))

    def __len__(self) -> int:
        return len(self._by_number)


# ----- helpers -----------------------------------------------------------

def _canon(law: str) -> str:
    return re.sub(r"\s+", "", law).upper()


def _law_inner(canonical: str) -> str:
    """Strip Kuvendi code wrapper: KUV-08/L-247-KOD → 08/L-247."""
    m = re.match(r"^KUV-(\d{1,2}/L-\d{1,4})(?:-[A-Z]+)?$", canonical)
    return m.group(1) if m else canonical


def _parse_date(s: str | None) -> str | None:
    """`14.01.2019` → `2019-01-14`. Returns None if input is missing/malformed."""
    if not s:
        return None
    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$", s)
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


__all__ = ["LawCatalog", "LawCatalogEntry"]
