"""
Interactive demo of the full AvokAI pipeline INCLUDING LLM generation.

Runs three representative queries through `pipeline.answer(use_llm=True)`
and prints the full Albanian answer with citation annotations. Use this
to:

  1. Smoke-test that DEEPSEEK_API_KEY is configured correctly.
  2. Verify the model produces in-language answers with mandatory
     citations in the `[Neni N, Ligji X/L-Y]` form.
  3. Confirm the citation validator catches hallucinations.

Run:
    cd backend && venv/bin/python tests/eval/demo_llm_pipeline.py

Cost: ~$0.001-0.005 total (3 queries through DeepSeek-V4-Pro).
"""

from __future__ import annotations

import os
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
    # Direct citation: should hit citation_lookup, retrieve Article 5 of 02/L-10,
    # and produce a cited answer about animal welfare obligations.
    "Çfarë thotë Neni 5 i Ligjit 02/L-10?",

    # Status: should hit status_lookup, no LLM (deterministic answer from registry)
    "A është aktiv Ligji 04/L-051?",

    # Topic: should hit semantic_question, retrieve relevant law(s), and produce
    # a cited answer in Albanian.
    "Cilat janë dispozitat ligjore për shoqatat?",
]


def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print(
            "DEEPSEEK_API_KEY is not set. Add it to backend/.env and re-run.\n"
            "Get one from https://platform.deepseek.com/api_keys",
            file=sys.stderr,
        )
        sys.exit(2)

    for q in CASES:
        print("\n" + "=" * 76)
        print(f"Q: {q}")
        print("=" * 76)
        result = answer(q, use_llm=True)
        print(f"intent: {result.intent}  ({result.elapsed_ms}ms)")
        if result.llm_usage:
            u = result.llm_usage
            print(
                f"llm: {u['model']}  prompt={u['prompt_tokens']} "
                f"completion={u['completion_tokens']} "
                f"cached={u['cached_tokens']} "
                f"~${u['usd_cost_estimate']}"
            )
        if result.abolishment_warnings:
            for w in result.abolishment_warnings:
                print(f"  ⚠ {w}")
        print()
        print("ANSWER:")
        print(result.answer)
        if result.citation_summary:
            cs = result.citation_summary
            print(
                f"\nCitations: {cs.get('citations_verified')}/{cs.get('citations_total')} verified "
                f"(rate: {cs.get('verified_rate')})"
            )
        print(f"\nSources used ({len(result.sources)}):")
        for s in result.sources[:5]:
            meta = s.get("metadata") or {}
            print(
                f"  - {meta.get('law_number')!r} art={meta.get('article_number')!r} "
                f"score={s.get('score', 0):.3f}"
            )


if __name__ == "__main__":
    main()
