"""
Query-time BM25 rescoring of dense retrieval results.

This is hybrid sparse-dense search done *post-retrieval*, against the live
Pinecone index, without re-upserting any vectors. The trick: pull a wide
top-N (e.g. 50) from dense, compute BM25 over those N chunks' content,
then rescore by `alpha * dense_norm + (1 - alpha) * bm25_norm`.

Best at fixing the lexical-recall failure mode dense embeddings have:
queries that hinge on a single rare proper noun ("mitrovicë", "digjitalë",
"resurseve") where the right document doesn't make top-5 because dense
similarity is dominated by surrounding generic legalese.

Tradeoffs vs. an index-wide BM25:
- IDF is only over the retrieved pool, not the whole corpus → the rare-term
  advantage is smaller than a true hybrid index would give.
- But: zero re-upsert cost, no Pinecone schema change, runs at query time
  in tens of milliseconds, and is exactly what v2 hybrid will replace
  cleanly later.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from rank_bm25 import BM25Okapi


# Albanian stopwords — short, conservative list. Removing more words risks
# dropping useful context; this list is just the obvious noise.
ALBANIAN_STOPWORDS = frozenset({
    "dhe", "ose", "por", "se", "që", "qe", "këtë", "kete", "këtu", "ketu",
    "kjo", "ky", "këto", "keto", "këta", "keta", "një", "nje", "nuk", "është",
    "eshte", "janë", "jane", "kanë", "kane", "ka", "kishte", "ku", "si", "kur",
    "për", "per", "me", "në", "ne", "nga", "i", "e", "të", "te", "së", "se",
    "u", "u'", "po", "do", "duhet", "mund", "më", "me", "vetëm", "vetem",
    "këtij", "ketij", "kësaj", "kesaj", "këtyre", "ketyre",
    "ligji", "ligjit", "ligjin", "ligje", "ligjet",  # match too broadly to be useful as keywords
    "neni", "nenit", "nenin", "nenet", "neneve",
})


_TOKEN_RE = re.compile(r"[A-Za-zëçËÇ]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase Albanian tokenizer that strips stopwords and very short tokens."""
    if not text:
        return []
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < 3:
            continue
        if tok in ALBANIAN_STOPWORDS:
            continue
        out.append(tok)
    return out


def rescore(
    query: str,
    dense_results: list[dict[str, Any]],
    alpha: float = 0.5,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Reorder dense results using a 50/50 blend of dense and BM25 scores.

    Args:
        query: Original natural-language query.
        dense_results: List of dicts with at least `content` and `score`. The
            output preserves all other keys (metadata, etc.).
        alpha: Weight on the dense score in [0, 1]. 1.0 = pure dense (original
            order), 0.0 = pure BM25, 0.5 = balanced.
        top_k: How many results to return.
    """
    if not dense_results:
        return []
    if alpha >= 1.0:
        return dense_results[:top_k]

    contents = [(r.get("content") or "") for r in dense_results]
    tokenized = [tokenize(c) for c in contents]
    if not any(tokenized):
        # Nothing tokenizable in the result set → just return dense order
        return dense_results[:top_k]

    bm25 = BM25Okapi(tokenized)
    q_tokens = tokenize(query)
    if not q_tokens:
        return dense_results[:top_k]

    raw_bm25 = bm25.get_scores(q_tokens)
    dense_scores = [float(r.get("score") or 0.0) for r in dense_results]

    # Min-max normalize each score array independently so the blend is meaningful
    def _norm(xs: list[float]) -> list[float]:
        lo, hi = min(xs), max(xs)
        if hi - lo < 1e-9:
            return [0.0] * len(xs)
        return [(x - lo) / (hi - lo) for x in xs]

    dense_n = _norm(dense_scores)
    bm25_n = _norm(list(raw_bm25))

    combined = [alpha * d + (1 - alpha) * b for d, b in zip(dense_n, bm25_n)]
    order = sorted(range(len(dense_results)), key=lambda i: -combined[i])

    out = []
    for i in order[:top_k]:
        item = dict(dense_results[i])
        item["score"] = combined[i]
        item["_dense_score"] = dense_scores[i]
        item["_bm25_score"] = float(raw_bm25[i])
        out.append(item)
    return out


__all__ = ["rescore", "tokenize"]
