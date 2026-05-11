"""Smoke test for the end-to-end pipeline (retrieval + routing + validation).

Runs without DEEPSEEK_API_KEY by passing `use_llm=False`. Verifies that
each route lights up correctly and surfaces sensible sources.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from app.ai.pipeline import answer


CASES = [
    ("Përshëndetje!", "greeting"),
    ("Më jep një recetë për byrek.", "out_of_scope"),
    ("Çfarë thotë Neni 5 i Ligjit 02/L-10?", "citation_lookup"),
    ("A është aktiv Ligji 04/L-051?", "status_lookup"),
    ("Cilat janë dispozitat ligjore për shoqatat?", "semantic_question"),
]


def main() -> None:
    fails = 0
    for query, expected_intent in CASES:
        print(f"\n{'='*76}")
        print(f"Q: {query}")
        result = answer(query, use_llm=False)
        ok = result.intent == expected_intent
        marker = "✓" if ok else "✗"
        print(f"  {marker} intent={result.intent} (expected {expected_intent})")
        print(f"  reason: {result.route_trace.get('reason')}")
        print(f"  sources: {len(result.sources)}")
        print(f"  elapsed: {result.elapsed_ms}ms")
        if result.sources[:2]:
            for s in result.sources[:2]:
                meta = s.get("metadata") or {}
                print(
                    f"    - law={meta.get('law_number')!r} "
                    f"art={meta.get('article_number')!r} "
                    f"score={s.get('score', 0):.3f}"
                )
        if result.abolishment_warnings:
            print(f"  warnings: {result.abolishment_warnings}")
        if result.intent in ("greeting", "out_of_scope"):
            print(f"  answer: {result.answer[:120]}...")
        if not ok:
            fails += 1

    print()
    print(f"{'PASS' if fails == 0 else f'{fails} FAILED'}")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
