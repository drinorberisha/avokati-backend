"""
Eval for the curated legal-reasoning golden set (golden_queries_reasoning.json).

Two query shapes are scored differently:

  * r*** (issue_spotting_*): multi-issue fact patterns. We report
      - routing intent
      - law-level UNION recall@5: of expected_law_numbers_union, how many
        appear in the production top-5 sources the LLM actually sees
      - per-ISSUE coverage@5: for each issue, was >=1 of its expected laws
        retrieved (law-level) and was the exact (law, article) retrieved
      - DENSE-POOL diagnostic: is each union law present anywhere in the
        dense top-200? Distinguishes "absent from corpus/embedding" from
        "retrieved but dropped by BM25+rerank ranking".

  * a*** (atomic_decomposed): single-issue queries. Standard law-level and
    (law, article) hit@5 — these isolate retrieval from the multi-issue blur.

Retrieval-only (no LLM) — measures whether the right authorities are even
put in front of the model. Run:
    cd backend && venv/bin/python tests/eval/run_eval_reasoning.py
"""

from __future__ import annotations

import json
import os
import sys
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

from app.ai.pipeline import answer, _embed_query, _pinecone_index, DEFAULT_NAMESPACE, DENSE_POOL_SIZE  # noqa: E402

GOLDEN_PATH = EVAL_DIR / "golden_queries_reasoning.json"
RESULTS_DIR = EVAL_DIR / "results"


def _norm_law(s: str | None) -> str:
    return (s or "").strip().upper().replace(" ", "")


def _retrieved_pairs(sources: list[dict]) -> list[tuple[str, str | None]]:
    out = []
    for s in sources:
        m = s.get("metadata") or {}
        out.append((_norm_law(m.get("law_number")), str(m.get("article_number") or "").strip() or None))
    return out


def _dense_pool_laws(query: str, namespace: str) -> set[str]:
    """Law numbers present anywhere in the dense top-200 (pre BM25/rerank)."""
    emb = _embed_query(query)
    res = _pinecone_index().query(
        namespace=namespace, vector=emb, top_k=DENSE_POOL_SIZE, include_metadata=True,
    )
    return {_norm_law((m.metadata or {}).get("law_number")) for m in res.matches}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(GOLDEN_PATH),
                    help="Golden set path (default: golden_queries_reasoning.json; also works for golden_queries_civil_cases.json)")
    args = ap.parse_args()
    golden = json.load(open(args.file, encoding="utf-8"))
    ns = DEFAULT_NAMESPACE
    rows: list[dict[str, Any]] = []

    print(f"Reasoning eval: {len(golden['queries'])} queries · namespace={ns}\n")

    for q in golden["queries"]:
        qid = q["id"]
        result = answer(q["query"], use_llm=False)
        pairs = _retrieved_pairs(result.sources)
        laws_top5 = {l for l, _ in pairs}

        if q["subtype"].startswith("issue_spotting"):
            union = [_norm_law(x) for x in q.get("expected_law_numbers_union", [])]
            union_hit = [l for l in union if l in laws_top5]
            pool_laws = _dense_pool_laws(q["query"], ns)
            union_in_pool = [l for l in union if l in pool_laws]

            issues = q.get("issues", [])
            issue_law_cov = 0
            issue_art_cov = 0
            issue_detail = []
            for iss in issues:
                exp_laws = {_norm_law(x) for x in iss.get("expected_law_numbers", [])}
                exp_arts = {str(a).strip() for a in iss.get("expected_article_numbers", [])}
                law_ok = bool(exp_laws & laws_top5)
                art_ok = any(l in exp_laws and a in exp_arts for (l, a) in pairs)
                issue_law_cov += law_ok
                issue_art_cov += art_ok
                issue_detail.append({"issue": iss["issue"][:60], "law@5": law_ok, "art@5": art_ok})

            n_iss = len(issues) or 1
            row = {
                "id": qid, "subtype": q["subtype"], "intent": result.intent,
                "union_recall@5": round(len(union_hit) / (len(union) or 1), 3),
                "union_hit": union_hit, "union_total": len(union),
                "union_recoverable_in_pool@200": round(len(union_in_pool) / (len(union) or 1), 3),
                "issue_law_coverage@5": round(issue_law_cov / n_iss, 3),
                "issue_article_coverage@5": round(issue_art_cov / n_iss, 3),
                "issues": issue_detail,
                "known_gaps": q.get("known_gaps", []),
            }
            print(f"[{qid}] intent={result.intent}  union_recall@5={row['union_recall@5']} "
                  f"(pool@200={row['union_recoverable_in_pool@200']})  "
                  f"issue_law_cov@5={row['issue_law_coverage@5']}  issue_art_cov@5={row['issue_article_coverage@5']}")
            for d in issue_detail:
                print(f"     {'L' if d['law@5'] else '.'}{'A' if d['art@5'] else '.'}  {d['issue']}")
        else:
            exp_laws = {_norm_law(x) for x in q.get("expected_law_numbers", [])}
            exp_arts = {str(a).strip() for a in q.get("expected_article_numbers", [])}
            law_ok = bool(exp_laws & laws_top5)
            art_ok = any(l in exp_laws and a in exp_arts for (l, a) in pairs)
            row = {"id": qid, "subtype": q["subtype"], "intent": result.intent,
                   "law_hit@5": law_ok, "article_hit@5": art_ok,
                   "expected": q.get("expected_law_numbers"), "expected_art": q.get("expected_article_numbers")}
            print(f"[{qid}] {'OK ' if law_ok else 'MISS'} law@5={law_ok} art@5={art_ok}  "
                  f"{q['query'][:55]}  -> exp {q.get('expected_law_numbers')} {q.get('expected_article_numbers')}")
        rows.append(row)

    # aggregates
    atomic = [r for r in rows if r["subtype"] == "atomic_decomposed"]
    reason = [r for r in rows if r["subtype"].startswith("issue_spotting")]
    print("\n" + "=" * 60)
    if atomic:
        print("ATOMIC: law_hit@5=%.2f  article_hit@5=%.2f  (n=%d)" % (
            sum(r["law_hit@5"] for r in atomic) / len(atomic),
            sum(r["article_hit@5"] for r in atomic) / len(atomic), len(atomic)))
    if reason:
        print("REASONING: mean union_recall@5=%.2f  mean issue_law_cov@5=%.2f  mean issue_art_cov@5=%.2f  (n=%d)" % (
            sum(r["union_recall@5"] for r in reason) / len(reason),
            sum(r["issue_law_coverage@5"] for r in reason) / len(reason),
            sum(r["issue_article_coverage@5"] for r in reason) / len(reason), len(reason)))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"eval_reasoning_{ts}.json"
    json.dump({"timestamp": ts, "namespace": ns, "rows": rows}, out.open("w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
