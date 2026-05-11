"""
Run AvokAI retrieval evaluation against the production Pinecone index.

Self-contained — talks to Pinecone and OpenAI directly via env vars, so it
does not need the backend's full settings stack to import cleanly. The
search behavior mirrors what `vector_store.search()` does at runtime
(same embedding model, same index, same namespace).

Required env vars (read from process env or backend/.env):
    OPENAI_API_KEY
    OPENAI_EMBEDDING_MODEL   (default: text-embedding-3-large)
    PINECONE_API_KEY
    PINECONE_INDEX_NAME      (default: lawvector)
    PINECONE_NAMESPACE       (default: default)

Run:
    cd backend && venv/bin/python tests/eval/run_eval.py

Output:
    backend/tests/eval/results/eval_<timestamp>.json
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
BACKEND_ROOT = EVAL_DIR.parents[1]  # backend/

# Load backend/.env so PINECONE/OPENAI keys are available
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

GOLDEN_PATH = EVAL_DIR / "golden_queries.json"
RESULTS_DIR = EVAL_DIR / "results"


def _load_golden() -> dict[str, Any]:
    with GOLDEN_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _norm_law(law: str | None) -> str:
    """Normalize a law number for comparison: strip whitespace, lowercase."""
    if not law:
        return ""
    return law.strip().lower().replace(" ", "")


def _extract_retrieved_laws(results: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    """Pull (law_number, article_number) from each retrieved chunk's metadata."""
    out: list[tuple[str, str | None]] = []
    for r in results:
        meta = r.get("document_metadata") or {}
        law = _norm_law(meta.get("law_number") or meta.get("title"))
        art = meta.get("article_number")
        if art is not None:
            art = str(art).strip()
        out.append((law, art))
    return out


def _hit_rank(retrieved: list[tuple[str, str | None]], expected_laws: list[str], expected_arts: list[str] | None) -> int | None:
    """Return 1-based rank of first relevant hit, or None."""
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


def _build_search_client():
    """Mirror what `app.ai.retrieval.vector_store.VectorStoreClient` does at runtime."""
    from openai import OpenAI
    from pinecone import Pinecone

    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embed_model = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index_name = os.environ.get("PINECONE_INDEX_NAME", "lawvector")
    namespace = os.environ.get("PINECONE_NAMESPACE", "default")
    index = pc.Index(index_name)

    def search(query: str, top_k: int = 10) -> list[dict[str, Any]]:
        emb = openai_client.embeddings.create(model=embed_model, input=[query]).data[0].embedding
        res = index.query(namespace=namespace, vector=emb, top_k=top_k, include_metadata=True)
        out = []
        for m in res.matches:
            meta = dict(m.metadata or {})
            out.append({
                "document_metadata": meta,
                "score": float(m.score) if m.score is not None else 0.0,
                "content": meta.get("content") or meta.get("text") or "",
            })
        return out

    print(f"Pinecone index={index_name} namespace={namespace} embed_model={embed_model}")
    return search


async def run() -> dict[str, Any]:
    search = _build_search_client()

    golden = _load_golden()
    queries = golden["queries"]

    per_query: list[dict[str, Any]] = []
    print(f"Running {len(queries)} queries...\n")

    for idx, q in enumerate(queries, start=1):
        t0 = time.time()
        try:
            results = search(q["query"], top_k=10)
        except Exception as e:
            print(f"  [{q['id']}] ERROR: {e}")
            per_query.append({**q, "error": str(e), "retrieved": [], "elapsed_ms": 0})
            continue
        elapsed_ms = int((time.time() - t0) * 1000)

        retrieved = _extract_retrieved_laws(results)

        # Loose: any expected law present
        rank_loose = _hit_rank(retrieved, q["expected_law_numbers"], None)
        # Strict: exact (law, article) — only meaningful when article ground truth exists
        rank_strict = _hit_rank(retrieved, q["expected_law_numbers"], q.get("expected_article_numbers"))

        per_query.append({
            "id": q["id"],
            "category": q["category"],
            "query": q["query"],
            "expected_law_numbers": q["expected_law_numbers"],
            "expected_article_numbers": q.get("expected_article_numbers"),
            "retrieved": [{"law": l, "article": a, "score": r.get("score")} for (l, a), r in zip(retrieved, results)],
            "rank_loose": rank_loose,
            "rank_strict": rank_strict,
            "elapsed_ms": elapsed_ms,
        })

        marker = "✓" if rank_loose and rank_loose <= 5 else "✗"
        print(f"  [{q['id']}] {marker} ({q['category']:>16s}) rank_loose={rank_loose} rank_strict={rank_strict} {elapsed_ms}ms")
        print(f"      Q: {q['query'][:90]}")
        if rank_loose is None:
            print(f"      retrieved: {[l for l, _ in retrieved[:3]]}")

    # ----- aggregate -----
    def _metric(rows: list[dict[str, Any]], strict: bool, k: int) -> float:
        if not rows:
            return 0.0
        key = "rank_strict" if strict else "rank_loose"
        hits = sum(1 for r in rows if r.get(key) is not None and r[key] <= k)
        return hits / len(rows)

    def _mrr(rows: list[dict[str, Any]], strict: bool = False) -> float:
        if not rows:
            return 0.0
        key = "rank_strict" if strict else "rank_loose"
        rrs = [(1.0 / r[key]) if r.get(key) else 0.0 for r in rows]
        return sum(rrs) / len(rows)

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

    overall = {
        "count": len(per_query),
        "recall_at_5_loose": round(_metric(per_query, False, 5), 3),
        "recall_at_10_loose": round(_metric(per_query, False, 10), 3),
        "recall_at_5_strict": round(_metric(per_query, True, 5), 3),
        "mrr_loose": round(_mrr(per_query, False), 3),
        "mrr_strict": round(_mrr(per_query, True), 3),
        "p50_latency_ms": int(statistics.median([r["elapsed_ms"] for r in per_query if r["elapsed_ms"]])) if per_query else 0,
        "p95_latency_ms": int(sorted([r["elapsed_ms"] for r in per_query if r["elapsed_ms"]])[int(len(per_query) * 0.95) - 1]) if len(per_query) >= 5 else 0,
    }

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "golden_seed": golden.get("seed"),
        "overall": overall,
        "by_category": by_cat,
        "per_query": per_query,
    }


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = asyncio.run(run())

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"eval_{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("OVERALL")
    print("=" * 60)
    for k, v in payload["overall"].items():
        print(f"  {k:>22s}: {v}")
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
