"""
Abstention eval for out-of-corpus constitutional-theory questions
(golden_queries_constitutional.json).

The product correctly refuses MOST questions it can't ground, but keyword
collisions ('konfederata' -> trade-union law) produce confidently-cited WRONG
answers. This harness measures both.

Modes:
  (default, retrieval-only, cheap): reports the TOP source score per query.
      A low max score is the signal a relevance-floor gate would use to force
      abstention. Shows the score distribution that motivates the threshold.
  --use-llm: runs full generation and classifies each query as
      REFUSED (good), ANSWERED-NO-CITE (acceptable general answer), or
      FABRICATED (>=1 verified citation to a statute -> hard FAIL for an
      out-of-corpus question).

Run:
    cd backend && venv/bin/python tests/eval/run_eval_abstention.py
    cd backend && venv/bin/python tests/eval/run_eval_abstention.py --use-llm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = EVAL_DIR.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from app.ai.pipeline import answer  # noqa: E402

GOLDEN_PATH = EVAL_DIR / "golden_queries_constitutional.json"
REFUSAL_MARKERS = ("nuk kam informacion të mjaftueshëm", "do not have enough information")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--use-llm", action="store_true", help="Generate answers and classify refuse/fabricate")
    args = p.parse_args()

    golden = json.load(GOLDEN_PATH.open(encoding="utf-8"))
    queries = golden["queries"]
    print(f"Abstention eval: {len(queries)} queries · use_llm={args.use_llm}\n")

    refused = fabricated = answered_nocite = 0
    low_score = 0  # top score below the candidate floor
    FLOOR = 0.40

    for q in queries:
        r = answer(q["query"], use_llm=args.use_llm)
        top = max((float(s.get("score") or 0.0) for s in r.sources), default=0.0)
        below = top < FLOOR
        low_score += below

        if not args.use_llm:
            flag = "collision?" if q.get("collision_risk") else ""
            print(f"[{q['id']}] {q['type']:<20} top_score={top:.3f} {'(below floor)' if below else '':<14} {flag}")
            continue

        ans = (r.answer or "").lower()
        verified = [c for c in r.citations if c.get("verified")]
        is_refusal = any(m in ans for m in REFUSAL_MARKERS)
        if verified and not is_refusal:
            verdict = "FABRICATED"
            fabricated += 1
        elif is_refusal or not verified:
            verdict = "REFUSED" if is_refusal else "ANSWERED-NO-CITE"
            refused += is_refusal
            answered_nocite += (0 if is_refusal else 1)
        cites = [(c["law_number"], c["article_number"]) for c in verified]
        mark = "  <-- HARD FAIL" if verdict == "FABRICATED" else ""
        print(f"[{q['id']}] {verdict:<17} top={top:.3f} cites={cites} {mark}")

    print("\n" + "=" * 60)
    n = len(queries)
    print(f"top source score < {FLOOR}: {low_score}/{n}  "
          f"({'this many would be caught by a relevance floor' if not args.use_llm else ''})")
    if args.use_llm:
        print(f"REFUSED (good): {refused}/{n}")
        print(f"ANSWERED-NO-CITE (acceptable): {answered_nocite}/{n}")
        print(f"FABRICATED (hard fail): {fabricated}/{n}")


if __name__ == "__main__":
    main()
