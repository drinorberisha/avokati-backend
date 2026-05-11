"""
Phase 2 hybrid eval: citation parser + semantic fallback.

Same metrics + golden set as `run_eval.py`, but routing each query through:
  1. Citation parser. If a `(law_number, article?)` is parsed → metadata
     filter on Pinecone, optionally post-filtered by `## Neni N` in content.
  2. Otherwise → standard semantic search (the current production path).

This is the first measurable retrieval win against the live `default`
namespace. Compares directly to `eval_<baseline>.json` for the delta.

Run:
    cd backend && venv/bin/python tests/eval/run_eval_hybrid.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = EVAL_DIR.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from app.ai.citation import parse_citation, lookup_by_citation  # noqa: E402
from app.ai.abolishment import (  # noqa: E402
    AbolishmentRegistry,
    is_status_query,
    render_synthetic_chunks,
)
from app.ai.bm25_rescore import rescore as bm25_rescore  # noqa: E402

# Pull a wide pool from dense before rescoring. Larger pool → BM25 has more
# IDF signal and more chances to surface the right answer. Empirically, on
# this corpus, the right chunk for rare-term queries can sit at dense rank
# 100+ ("digjitalë" → 08/L-295 at dense rank 107), so we pull 200 to give
# BM25 room to surface those. Latency cost is ~+200ms per query.
DENSE_POOL_SIZE = 200
# Lower alpha = more BM25 weight. Tuned empirically: at 0.5 the dense score
# dominates and rare-term BM25 wins are washed out. 0.2 lets lexical match
# pull through ("organi" 23→3, "resurseve" 24→7).
RESCORE_ALPHA = 0.2

GOLDEN_PATH = EVAL_DIR / "golden_queries.json"
RESULTS_DIR = EVAL_DIR / "results"


def _load_golden() -> dict[str, Any]:
    with GOLDEN_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _norm_law(law: str | None) -> str:
    if not law:
        return ""
    # Strip the metadata-name "NR." prefix that some indexed entries carry,
    # so the normalized form matches the canonical form the citation parser
    # produces and the ground-truth uses.
    s = law.strip().lower().replace(" ", "")
    for pref in ("nr.", "nr", "n."):
        if s.startswith(pref):
            s = s[len(pref):]
            break
    return s


def _extract_retrieved_laws(results: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for r in results:
        meta = r.get("document_metadata") or r.get("metadata") or {}
        law = _norm_law(meta.get("law_number") or meta.get("title"))
        # The live index has article_number=None; try to recover it from content
        art = meta.get("article_number")
        if not art:
            content = r.get("content") or meta.get("content") or meta.get("text") or ""
            import re
            m = re.search(r"(?:^|\n)\s*#{0,3}\s*Neni\s+(\d{1,3})\b", content[:300])
            art = m.group(1) if m else None
        if art is not None:
            art = str(art).strip()
        out.append((law, art))
    return out


def _hit_rank(retrieved: list[tuple[str, str | None]], expected_laws: list[str], expected_arts: list[str] | None) -> int | None:
    expected_norm = {_norm_law(x) for x in expected_laws}
    arts_norm = {str(a).strip() for a in expected_arts} if expected_arts else None
    for i, (law, art) in enumerate(retrieved, start=1):
        if law not in expected_norm:
            continue
        if arts_norm is None:
            return i
        if art and art in arts_norm:
            return i
    return None


def _build_clients(namespace_override: str | None = None):
    from openai import OpenAI
    from pinecone import Pinecone

    oai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embed_model = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index_name = os.environ.get("PINECONE_INDEX_NAME", "lawvector")
    namespace = namespace_override or os.environ.get("PINECONE_NAMESPACE", "default")
    index = pc.Index(index_name)
    print(f"Pinecone index={index_name} namespace={namespace} embed_model={embed_model}\n")
    return oai, embed_model, index, namespace


def _semantic_search(oai, embed_model, index, namespace, query: str, top_k: int = 10, *, with_bm25: bool = True) -> list[dict[str, Any]]:
    """Dense retrieval with optional BM25 rescoring of a wider pool.

    Pulls `DENSE_POOL_SIZE` chunks via cosine similarity, then if `with_bm25`
    is on, rescores them with `bm25_rescore.rescore()` and returns the
    top-`top_k`. The BM25 pool size is intentionally larger than the
    returned k so rare-term lexical matches can promote chunks that dense
    alone ranked outside the top-k.
    """
    pool_size = max(top_k, DENSE_POOL_SIZE) if with_bm25 else top_k
    emb = oai.embeddings.create(model=embed_model, input=[query]).data[0].embedding
    res = index.query(namespace=namespace, vector=emb, top_k=pool_size, include_metadata=True)
    pool = []
    for m in res.matches:
        meta = dict(m.metadata or {})
        pool.append({
            "document_metadata": meta,
            "score": float(m.score) if m.score is not None else 0.0,
            "content": meta.get("content") or meta.get("text") or "",
        })
    if not with_bm25:
        return pool[:top_k]
    return bm25_rescore(query, pool, alpha=RESCORE_ALPHA, top_k=top_k)


_DUMMY_VECTOR_CACHE: list[float] | None = None


def _dummy_vector(oai, embed_model: str) -> list[float]:
    """Pinecone requires a vector even on filter-only queries. Embed once, reuse."""
    global _DUMMY_VECTOR_CACHE
    if _DUMMY_VECTOR_CACHE is None:
        _DUMMY_VECTOR_CACHE = oai.embeddings.create(model=embed_model, input=["placeholder"]).data[0].embedding
    return _DUMMY_VECTOR_CACHE


async def run(namespace_override: str | None = None) -> dict[str, Any]:
    oai, embed_model, index, namespace = _build_clients(namespace_override)
    abolishment = AbolishmentRegistry.get()
    golden = _load_golden()
    queries = golden["queries"]

    per_query: list[dict[str, Any]] = []
    print(f"Running {len(queries)} queries through hybrid pipeline...\n")

    for q in queries:
        t0 = time.time()
        path = "semantic"
        results: list[dict[str, Any]] = []
        try:
            citation = parse_citation(q["query"])
            status_query = citation is not None and is_status_query(q["query"])

            if status_query:
                # Status questions are answered from abolishment_relations.json,
                # not from Pinecone. Synthetic chunks let the same eval metric
                # apply (the abolished and abolishing law numbers both count).
                info = abolishment.lookup(citation.law_number)
                results = render_synthetic_chunks(info)
                path = f"abolishment:{info.status}"
                if info.status == "unknown":
                    # No relation on record → fall back to semantic in case
                    # there's something topical worth showing
                    results = _semantic_search(oai, embed_model, index, namespace, q["query"], top_k=10)
                    path = "abolishment_unknown→semantic"

            elif citation is not None:
                lookup = lookup_by_citation(
                    citation=citation,
                    pinecone_index=index,
                    namespace=namespace,
                    dummy_vector=_dummy_vector(oai, embed_model),
                    top_k=10,
                )
                results = [
                    {"document_metadata": m["metadata"], "score": m["score"], "content": m["content"]}
                    for m in lookup["matches"]
                ]
                if results:
                    path = f"citation:{lookup['article_match_quality']}"
                else:
                    # Law not in index → fall back to semantic so we don't
                    # return zero results when something topical might help
                    results = _semantic_search(oai, embed_model, index, namespace, q["query"], top_k=10)
                    path = "citation_miss→semantic"
            else:
                results = _semantic_search(oai, embed_model, index, namespace, q["query"], top_k=10)
                path = "semantic"
        except Exception as e:
            print(f"  [{q['id']}] ERROR: {e}")
            per_query.append({**q, "error": str(e), "retrieved": [], "elapsed_ms": 0, "path": path})
            continue

        elapsed_ms = int((time.time() - t0) * 1000)
        retrieved = _extract_retrieved_laws(results)
        rank_loose = _hit_rank(retrieved, q["expected_law_numbers"], None)
        rank_strict = _hit_rank(retrieved, q["expected_law_numbers"], q.get("expected_article_numbers"))

        per_query.append({
            "id": q["id"],
            "category": q["category"],
            "query": q["query"],
            "expected_law_numbers": q["expected_law_numbers"],
            "expected_article_numbers": q.get("expected_article_numbers"),
            "path": path,
            "retrieved": [{"law": l, "article": a, "score": r.get("score")} for (l, a), r in zip(retrieved, results)],
            "rank_loose": rank_loose,
            "rank_strict": rank_strict,
            "elapsed_ms": elapsed_ms,
        })

        marker = "✓" if rank_loose and rank_loose <= 5 else "✗"
        print(f"  [{q['id']}] {marker} ({q['category']:>16s}) path={path:>26s} rank_loose={rank_loose} rank_strict={rank_strict} {elapsed_ms}ms")
        if rank_loose is None:
            print(f"      Q: {q['query'][:90]}")
            print(f"      retrieved: {[l for l, _ in retrieved[:3]]}")

    def _metric(rows, strict, k):
        if not rows: return 0.0
        key = "rank_strict" if strict else "rank_loose"
        return sum(1 for r in rows if r.get(key) and r[key] <= k) / len(rows)

    def _mrr(rows, strict=False):
        if not rows: return 0.0
        key = "rank_strict" if strict else "rank_loose"
        return sum((1.0 / r[key]) if r.get(key) else 0.0 for r in rows) / len(rows)

    by_cat: dict[str, dict[str, Any]] = {}
    for cat in sorted({r["category"] for r in per_query}):
        rows = [r for r in per_query if r["category"] == cat]
        by_cat[cat] = {
            "count": len(rows),
            "recall_at_5_loose": round(_metric(rows, False, 5), 3),
            "recall_at_10_loose": round(_metric(rows, False, 10), 3),
            "recall_at_5_strict": round(_metric(rows, True, 5), 3),
            "mrr_loose": round(_mrr(rows, False), 3),
        }

    by_path: dict[str, int] = {}
    for r in per_query:
        by_path[r["path"]] = by_path.get(r["path"], 0) + 1

    overall = {
        "count": len(per_query),
        "recall_at_5_loose": round(_metric(per_query, False, 5), 3),
        "recall_at_10_loose": round(_metric(per_query, False, 10), 3),
        "recall_at_5_strict": round(_metric(per_query, True, 5), 3),
        "mrr_loose": round(_mrr(per_query, False), 3),
        "mrr_strict": round(_mrr(per_query, True), 3),
        "p50_latency_ms": int(statistics.median([r["elapsed_ms"] for r in per_query])) if per_query else 0,
        "p95_latency_ms": int(sorted(r["elapsed_ms"] for r in per_query)[max(0, int(len(per_query) * 0.95) - 1)]) if per_query else 0,
    }

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "pipeline": "hybrid_v2: citation_parser + abolishment + dense_top50→bm25_rescore",
        "golden_seed": golden.get("seed"),
        "overall": overall,
        "by_category": by_cat,
        "by_path": by_path,
        "per_query": per_query,
    }


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--namespace", help="Pinecone namespace to eval against (default: from env)")
    p.add_argument("--tag", default="hybrid", help="Filename tag for the output JSON")
    args = p.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = asyncio.run(run(args.namespace))

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    ns_tag = (args.namespace or "default").replace("/", "_")
    out_path = RESULTS_DIR / f"eval_{args.tag}_{ns_tag}_{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("OVERALL (hybrid v1)")
    print("=" * 60)
    for k, v in payload["overall"].items():
        print(f"  {k:>22s}: {v}")
    print()
    print("BY PATH")
    print("=" * 60)
    for p, c in payload["by_path"].items():
        print(f"  {p:>30s}: {c}")
    print()
    print("BY CATEGORY")
    print("=" * 60)
    for cat, m in payload["by_category"].items():
        print(f"  {cat}")
        for k, v in m.items():
            print(f"    {k:>22s}: {v}")
    print()
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
