"""
Cross-encoder reranker for the AvokAI semantic_question retrieval path.

Model: `BAAI/bge-reranker-v2-m3` — a multilingual cross-encoder that jointly
attends to (query, chunk) pairs and produces a relevance score. Substantially
better precision than the bi-encoder cosine baseline on topic-category queries,
which were the weakest cell of the v2 eval (50% Recall@5).

How it's wired:

    semantic_question path in pipeline.py:
        Pinecone top-200 (dense)
            └── bm25_rescore (alpha=0.2) → top-20 by hybrid score
                └── rerank(query, top-20) → top-K by cross-encoder score
                    └── LLM gets the reranked top-K

The reranker runs over the BM25-reranked pool, not directly over the dense top-200,
because (a) BM25 already cuts noise cheaply and (b) cross-encoder inference is
quadratic in candidate count.

Operational characteristics:
  - Model: ~1.1 GB on disk, ~600 MB RAM at inference
  - Latency: ~200-300 ms for 20 candidates on 1 vCPU (CPU-only)
  - Cold start: ~3-5 s to download + load on first request after deploy
  - Cloud Run memory bump required: 1Gi → 2Gi

Feature-flagged via `RERANKER_ENABLED` env var (default: true). Set to "false"
to bypass the reranker entirely — useful for debugging or rolling back if the
model causes problems in prod.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_ENABLED = os.environ.get("RERANKER_ENABLED", "true").lower() in ("true", "1", "yes")
RERANK_POOL_SIZE = int(os.environ.get("RERANKER_POOL_SIZE", "20"))

_model: Any = None
_model_lock = threading.Lock()


def is_enabled() -> bool:
    return RERANKER_ENABLED


def _get_model():
    """Lazy-load the cross-encoder on first use.

    The model takes 3-5 s to load. We do it under a lock so concurrent first-
    requests don't double-load it. Subsequent calls hit the cached instance.

    Returns None if the model can't be loaded (missing dep, network failure
    during download, etc.) — callers should treat this as "reranking unavailable"
    and pass through the pre-rerank order rather than failing the request.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import CrossEncoder

            # max_length=256 covers ~99% of v2 article chunks (Albanian legal
            # text is dense — 256 BPE tokens ≈ 1k chars ≈ first paragraph or
            # two). Halving from 512 gives ~2x CPU inference speed on the
            # Cloud Run 2-vCPU instance where the model is the largest part
            # of per-query latency.
            logger.info("loading cross-encoder reranker: %s", DEFAULT_MODEL)
            _model = CrossEncoder(DEFAULT_MODEL, max_length=256)
            logger.info("reranker ready")
        except Exception as e:
            logger.error("failed to load reranker (%s): %r", DEFAULT_MODEL, e)
            _model = None
    return _model


def warmup() -> bool:
    """Trigger model load now rather than on first request.

    Call from FastAPI lifespan startup to amortize the 3-5 s model load
    before any user-facing query hits the reranker. Returns True if the
    model loaded successfully, False otherwise.
    """
    if not RERANKER_ENABLED:
        return False
    return _get_model() is not None


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Re-rank candidates by cross-encoder relevance to query.

    Args:
        query: the user's natural-language question
        candidates: list of dicts with `content` field (and any other keys to preserve)
        top_k: number of results to return after reranking

    Returns the top_k candidates sorted by cross-encoder score, descending.
    Each returned item gets an extra `_rerank_score` key for downstream logging.

    Failure mode: if the model isn't loaded (disabled, download failed, etc.),
    returns the first `top_k` candidates unchanged. This is intentional —
    a reranker failure should never block an answer.
    """
    if not RERANKER_ENABLED or not candidates:
        return candidates[:top_k]

    model = _get_model()
    if model is None:
        return candidates[:top_k]

    # Cap input to the pool size we tuned for; if caller passes more, ignore the tail.
    pool = candidates[:RERANK_POOL_SIZE]

    pairs = [(query, (c.get("content") or "")[:2000]) for c in pool]
    try:
        scores = model.predict(pairs)
    except Exception as e:
        logger.warning("reranker.predict failed: %r — falling back to input order", e)
        return candidates[:top_k]

    order = sorted(range(len(pool)), key=lambda i: -float(scores[i]))
    out: list[dict[str, Any]] = []
    for i in order[:top_k]:
        item = dict(pool[i])
        item["_rerank_score"] = float(scores[i])
        # Preserve the pre-rerank score under a sub-key so downstream code can
        # still see what BM25-hybrid thought.
        if "score" in item:
            item["_pre_rerank_score"] = item["score"]
        item["score"] = float(scores[i])
        out.append(item)
    return out


__all__ = ["rerank", "warmup", "is_enabled", "RERANK_POOL_SIZE", "DEFAULT_MODEL"]
