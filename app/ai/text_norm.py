"""
Shared Albanian text normalization for diacritic/dialect-insensitive matching.

Kosovo users type legal Albanian inconsistently — with or without diacritics
("çështje" vs "ceshtje"), and in dialect forms. Matching (routing keywords,
BM25 lexical scoring) should treat those as the same. `fold` is the single
source of truth for that normalization; the router's article-only filter and
the BM25 tokenizer both use it, so behavior stays consistent across the pipeline.

Deliberately minimal and lossless-in-meaning: lowercase + fold the two Albanian
diacritics (ë→e, ç→c). We do NOT fold `q`→`c` here — `q` is a real letter
(qytet, qeveri); callers that want the informal q-spellings of specific words
list them explicitly. Deeper Geg morphology (me punue ↔ të punojë) is out of
scope — that's dialect translation, not normalization.
"""

from __future__ import annotations


def fold(s: str) -> str:
    """Return a diacritic/dialect-insensitive form: lowercase, ë→e, ç→c.

    ë→e and ç→c are 1:1 character swaps, so token length is preserved (callers
    relying on length checks are unaffected).
    """
    return s.lower().replace("ë", "e").replace("ç", "c")


__all__ = ["fold"]
