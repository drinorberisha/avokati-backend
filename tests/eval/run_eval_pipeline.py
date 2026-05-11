"""
End-to-end pipeline eval.

Runs every golden query through `pipeline.answer()` and measures:
  - routing correctness vs the gold-set's `category` field
  - retrieval recall (loose @5/@10, strict @5) — same as `run_eval_hybrid`
  - abolishment warning surfacing rate
  - LLM-side metrics when DEEPSEEK_API_KEY is set:
      * citation verification rate
      * mean cost per query

Without LLM (default if no key): runs in retrieval-only mode and skips
the citation/cost columns. The retrieval numbers should match
`run_eval_hybrid.py` since they share the same primitives.

Run:
    cd backend && venv/bin/python tests/eval/run_eval_pipeline.py
    cd backend && venv/bin/python tests/eval/run_eval_pipeline.py --use-llm
    cd backend && venv/bin/python tests/eval/run_eval_pipeline.py --namespace default
"""

from __future__ import annotations

import argparse
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

from app.ai.pipeline import answer  # noqa: E402

GOLDEN_PATH = EVAL_DIR / "golden_queries.json"
RESULTS_DIR = EVAL_DIR / "results"


# Map golden-set categories to expected pipeline intents. The gold-set
# annotates queries by what we wanted to test; the router decides what
# path the system actually takes. Mismatch is a routing bug.
EXPECTED_INTENT_BY_CATEGORY = {
    "direct_citation": "citation_lookup",
    "status": "status_lookup",
    "topic": "semantic_question",
    "article_content": "semantic_question",
}


def _norm_law(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower().replace(" ", "")
    for prefix in ("nr.", "nr", "n."):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def _hit_rank(retrieved_laws: list[tuple[str, str | None]], expected_laws: list[str], expected_arts: list[str] | None) -> int | None:
    expected_norm = {_norm_law(x) for x in expected_laws}
    arts_norm = {str(a).strip() for a in expected_arts} if expected_arts else None
    for i, (law, art) in enumerate(retrieved_laws, start=1):
        if law not in expected_norm:
            continue
        if arts_norm is None:
            return i
        if art and art in arts_norm:
            return i
    return None


def _extract_retrieved(sources: list[dict[str, Any]]) -> list[tuple[str, str | None]]:
    """Pull (law_number, article_number) pairs from pipeline sources."""
    import re as _re
    out: list[tuple[str, str | None]] = []
    for s in sources:
        meta = s.get("metadata") or s.get("document_metadata") or {}
        law = _norm_law(meta.get("law_number"))
        art = meta.get("article_number")
        if not art:
            content = s.get("content") or meta.get("content") or ""
            m = _re.search(r"(?:^|\n)\s*#{0,3}\s*Neni\s+(\d{1,3})\b", content[:300])
            art = m.group(1) if m else None
        if art is not None:
            art = str(art).strip()
        out.append((law, art))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--use-llm", action="store_true",
                   help="Send queries to DeepSeek for full answer generation. Requires DEEPSEEK_API_KEY.")
    p.add_argument("--namespace", help="Pinecone namespace (defaults to PINECONE_NAMESPACE_V2 env or `default_v2`)")
    p.add_argument("--limit", type=int, help="Process only first N queries (for quick iteration)")
    args = p.parse_args()

    if args.use_llm and not os.environ.get("DEEPSEEK_API_KEY"):
        print("ERROR: --use-llm requires DEEPSEEK_API_KEY in env / backend/.env", file=sys.stderr)
        sys.exit(2)

    with GOLDEN_PATH.open(encoding="utf-8") as f:
        golden = json.load(f)
    queries = golden["queries"]
    if args.limit:
        queries = queries[:args.limit]

    print(f"Pipeline eval: {len(queries)} queries · use_llm={args.use_llm} · namespace={args.namespace or 'default_v2'}\n")

    per_query: list[dict[str, Any]] = []
    routing_correct = 0
    abolishment_surfaced = 0
    abolishment_relevant = 0
    citation_total = 0
    citation_verified = 0
    total_cost_usd = 0.0

    for q in queries:
        try:
            result = answer(
                q["query"],
                namespace=args.namespace,
                use_llm=args.use_llm,
            )
        except Exception as e:
            per_query.append({**q, "error": repr(e)})
            print(f"  [{q['id']}] ERROR: {e}")
            continue

        retrieved = _extract_retrieved(result.sources)
        rank_loose = _hit_rank(retrieved, q["expected_law_numbers"], None)
        rank_strict = _hit_rank(retrieved, q["expected_law_numbers"], q.get("expected_article_numbers"))

        expected_intent = EXPECTED_INTENT_BY_CATEGORY.get(q["category"], "semantic_question")
        # citation_lookup queries can legitimately fall back to semantic_question
        # when their target law isn't in the index. Count both as routing-correct.
        intent_ok = (
            result.intent == expected_intent
            or (expected_intent == "citation_lookup" and result.intent == "semantic_question"
                and result.route_trace.get("citation_miss"))
        )
        if intent_ok:
            routing_correct += 1

        if result.abolishment_warnings:
            abolishment_surfaced += 1

        # Estimate the abolishment-relevance: if any retrieved law is in the
        # registry as abolished, the warning *should* fire.
        from app.ai.abolishment import AbolishmentRegistry
        registry = AbolishmentRegistry.get()
        retrieved_laws_set = {l for l, _ in retrieved if l}
        relevant = any(
            registry.lookup(law).status in ("fully_abolished", "partially_abolished")
            for law in retrieved_laws_set
        )
        if relevant:
            abolishment_relevant += 1

        cs = result.citation_summary or {}
        citation_total += int(cs.get("citations_total", 0) or 0)
        citation_verified += int(cs.get("citations_verified", 0) or 0)

        if result.llm_usage:
            total_cost_usd += float(result.llm_usage.get("usd_cost_estimate") or 0.0)

        per_query.append({
            "id": q["id"],
            "category": q["category"],
            "query": q["query"],
            "expected_law_numbers": q["expected_law_numbers"],
            "expected_article_numbers": q.get("expected_article_numbers"),
            "intent": result.intent,
            "expected_intent": expected_intent,
            "intent_ok": intent_ok,
            "rank_loose": rank_loose,
            "rank_strict": rank_strict,
            "elapsed_ms": result.elapsed_ms,
            "answer_preview": (result.answer or "")[:200],
            "warnings": result.abolishment_warnings,
            "citation_summary": cs,
            "llm_usage": result.llm_usage,
            "trace": result.route_trace,
        })

        marker_intent = "✓" if intent_ok else "✗"
        marker_loose = "✓" if rank_loose and rank_loose <= 5 else "✗"
        warn = " ⚠" if result.abolishment_warnings else ""
        print(f"  [{q['id']}] {marker_intent}{marker_loose} ({q['category']:>16s}) "
              f"intent={result.intent:>22s} rank_loose={rank_loose} {result.elapsed_ms}ms{warn}")

    n = len(per_query)
    def _r(rows, key, k):
        return sum(1 for r in rows if r.get(key) and r[key] <= k) / len(rows) if rows else 0.0
    def _mrr(rows, key):
        return sum((1.0 / r[key]) if r.get(key) else 0.0 for r in rows) / len(rows) if rows else 0.0

    overall = {
        "count": n,
        "routing_accuracy": round(routing_correct / n, 3) if n else 0.0,
        "recall_at_5_loose": round(_r(per_query, "rank_loose", 5), 3),
        "recall_at_10_loose": round(_r(per_query, "rank_loose", 10), 3),
        "recall_at_5_strict": round(_r(per_query, "rank_strict", 5), 3),
        "mrr_loose": round(_mrr(per_query, "rank_loose"), 3),
        "mrr_strict": round(_mrr(per_query, "rank_strict"), 3),
        "abolishment_surfaced_when_relevant": (
            round(abolishment_surfaced / abolishment_relevant, 3)
            if abolishment_relevant else None
        ),
        "abolishment_relevant_count": abolishment_relevant,
        "citation_verified_rate": (
            round(citation_verified / citation_total, 3) if citation_total else None
        ),
        "citation_total": citation_total,
        "p50_latency_ms": int(statistics.median(r["elapsed_ms"] for r in per_query if "elapsed_ms" in r)) if per_query else 0,
        "p95_latency_ms": int(sorted(r["elapsed_ms"] for r in per_query if "elapsed_ms" in r)[max(0, int(n * 0.95) - 1)]) if n else 0,
        "total_llm_cost_usd": round(total_cost_usd, 6),
    }

    by_cat: dict[str, dict[str, Any]] = {}
    for cat in sorted({r["category"] for r in per_query}):
        rows = [r for r in per_query if r["category"] == cat]
        by_cat[cat] = {
            "count": len(rows),
            "routing_correct": round(sum(1 for r in rows if r.get("intent_ok")) / len(rows), 3) if rows else 0.0,
            "recall_at_5_loose": round(_r(rows, "rank_loose", 5), 3),
            "recall_at_5_strict": round(_r(rows, "rank_strict", 5), 3),
            "mrr_loose": round(_mrr(rows, "rank_loose"), 3),
        }

    print()
    print("=" * 60)
    print("OVERALL (pipeline)")
    print("=" * 60)
    for k, v in overall.items():
        print(f"  {k:>34s}: {v}")
    print()
    print("BY CATEGORY")
    for cat, m in by_cat.items():
        print(f"  {cat}")
        for k, v in m.items():
            print(f"    {k:>22s}: {v}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    suffix = "llm" if args.use_llm else "noLlm"
    ns_tag = (args.namespace or "default_v2").replace("/", "_")
    out_path = RESULTS_DIR / f"eval_pipeline_{ns_tag}_{suffix}_{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "namespace": args.namespace or "default_v2",
            "use_llm": args.use_llm,
            "overall": overall,
            "by_category": by_cat,
            "per_query": per_query,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
