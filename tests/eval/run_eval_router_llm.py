"""
Router benchmark: regex `classify()` vs LLM `classify_llm()` (V4-Flash).

Measures, over a labeled routing set (embedded stress cases + the golden set):

  1. LATENCY — per-call wall clock of the LLM router (cold call reported
     separately; regex is ~µs so it's the pure overhead), p50/p95, and the
     projected per-query overhead under the two deployment modes:
       - llm-first:  every query pays the LLM routing call
       - fallback:   only regex `default_fallthrough` queries pay it
  2. ACCURACY — intent vs expected label, for both routers.
  3. AGREEMENT — where the two routers disagree, listed for eyeballing.
  4. COST — actual token usage / USD per routed query (prompt-cache visible).

Run:
    cd backend && venv/bin/python tests/eval/run_eval_router_llm.py
    # options:
    #   --stress-only     skip the 40 golden queries (faster, cheaper)
    #   --limit N         cap total queries
    #   --out FILE        results JSON (default tests/eval/results/router_llm_bench_<ts>.json)

Requires DEEPSEEK_API_KEY in backend/.env. Cost for the full set (~68 queries)
is well under $0.01.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = EVAL_DIR.parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from app.ai.conversation import derive_context  # noqa: E402
from app.ai.router import classify  # noqa: E402
from app.ai.router_llm import classify_llm  # noqa: E402

GOLDEN_PATH = EVAL_DIR / "golden_queries.json"

# Expected intent per golden-set category. `article_content` queries name an
# article but are sometimes phrased topically, so both routes are acceptable.
_GOLDEN_CATEGORY_INTENTS = {
    "direct_citation": ["citation_lookup", "status_lookup"],
    "status": ["status_lookup"],
    "topic": ["semantic_question"],
    "article_content": ["citation_lookup", "semantic_question"],
}


# Hand-labeled stress set: the paraphrase / dialect / diacritic-free phrasings
# the regex router is known or suspected to miss. `expected` lists acceptable
# intents (first = preferred). `history` builds a ConversationContext.
STRESS_CASES: list[dict] = [
    # --- greetings, paraphrased (regex greeting is anchored ^...$) ---
    {"id": "s01", "query": "tungjatjeta, a mundesh me m'ndihmu?", "expected": ["greeting", "semantic_question"]},
    {"id": "s02", "query": "mirembrema avokat", "expected": ["greeting"]},
    {"id": "s03", "query": "flm shume per ndihmen", "expected": ["greeting"]},
    {"id": "s04", "query": "hej cka bon ti", "expected": ["greeting"]},
    # --- status, paraphrased (keywords outside STATUS_KEYWORDS) ---
    {"id": "s05", "query": "a ka ndryshu ligji i familjes?", "expected": ["status_lookup", "semantic_question"]},
    {"id": "s06", "query": "ligji 2004/32 a osht hala i vlefshem?", "expected": ["status_lookup"]},
    {"id": "s07", "query": "a e kane hjek ligjin per trashegimine?", "expected": ["status_lookup", "semantic_question"]},
    {"id": "s08", "query": "pershendetje, a eshte ne fuqi ligji 03/L-212?", "expected": ["status_lookup"]},
    # --- citation, informal / spelled-out / reordered ---
    {"id": "s09", "query": "Çfarë thotë neni 47 i ligjit 04/L-077?", "expected": ["citation_lookup"]},
    {"id": "s10", "query": "me trego çka shkruan te neni pese i kodit penal", "expected": ["citation_lookup", "semantic_question"]},
    {"id": "s11", "query": "neni 12, ligji per punen", "expected": ["citation_lookup"]},
    {"id": "s12", "query": "what does article 49 of law 03/L-212 say?", "expected": ["citation_lookup"]},
    # --- clarify: incomplete references ---
    {"id": "s13", "query": "çka thote neni 37?", "expected": ["clarify"]},
    {"id": "s14", "query": "me trego nenin 47 te ligjit 04", "expected": ["clarify"]},
    {"id": "s15", "query": "a mundesh me me tregu per nenin 15?", "expected": ["clarify"]},
    # --- out of scope / out of corpus, paraphrased ---
    {"id": "s16", "query": "çfare thote ligji gjerman per qirane?", "expected": ["out_of_scope"]},
    {"id": "s17", "query": "si funksionon gjykata evropiane e te drejtave te njeriut?", "expected": ["out_of_scope", "semantic_question"]},
    {"id": "s18", "query": "kush ishte hans kelsen dhe cka mendonte per normat?", "expected": ["out_of_scope"]},
    {"id": "s19", "query": "me trego receten e flijes", "expected": ["out_of_scope"]},
    {"id": "s20", "query": "a me ndihmon me nje diete per dobesim?", "expected": ["out_of_scope"]},
    # --- semantic: dialect, typos, English, colloquial ---
    {"id": "s21", "query": "sa dite pushimi me takojne ne vit?", "expected": ["semantic_question"]},
    {"id": "s22", "query": "a bon me punu pa kontrate pune?", "expected": ["semantic_question"]},
    {"id": "s23", "query": "shoku im me ka borxh 5000 euro dhe spo ma kthen, cka me bo?", "expected": ["semantic_question"]},
    {"id": "s24", "query": "how do i register an NGO in kosovo?", "expected": ["semantic_question"]},
    {"id": "s25", "query": "a lejohet puna e femijeve nen moshen 15 vjec?", "expected": ["semantic_question"]},
    {"id": "s26", "query": "cka thote ligji i punes per pushimin e lehonise?", "expected": ["semantic_question"]},
    # --- follow-ups (context inheritance) ---
    {"id": "s27", "query": "po neni 38 çka thote?", "expected": ["citation_lookup"],
     "history": [{"role": "user", "content": "Neni 37 i Ligjit 03/L-212"},
                 {"role": "assistant", "content": "Neni 37 i Ligjit 03/L-212 rregullon pushimin vjetor..."}]},
    {"id": "s28", "query": "a eshte ende ne fuqi ky ligj?", "expected": ["status_lookup"],
     "history": [{"role": "user", "content": "Neni 37 i Ligjit 03/L-212"},
                 {"role": "assistant", "content": "Neni 37 i Ligjit 03/L-212 rregullon pushimin vjetor..."}]},
]


def load_cases(stress_only: bool, limit: int | None) -> list[dict]:
    cases = list(STRESS_CASES)
    if not stress_only:
        data = json.loads(GOLDEN_PATH.read_text())
        for q in data["queries"]:
            expected = _GOLDEN_CATEGORY_INTENTS.get(q.get("category"))
            if not expected:
                continue
            cases.append({
                "id": q["id"],
                "query": q["query"],
                "expected": expected,
                "category": q.get("category"),
            })
    return cases[:limit] if limit else cases


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stress-only", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    cases = load_cases(args.stress_only, args.limit)
    print(f"Routing benchmark: {len(cases)} queries "
          f"({'stress only' if args.stress_only else 'stress + golden'})\n")

    rows: list[dict] = []
    llm_latencies: list[int] = []
    regex_latencies_us: list[float] = []
    total_cost = 0.0
    n_fallback = 0

    for i, case in enumerate(cases):
        ctx = derive_context(case.get("history")) if case.get("history") else None

        t0 = time.perf_counter()
        regex_dec = classify(case["query"], context=ctx)
        regex_us = (time.perf_counter() - t0) * 1e6
        regex_latencies_us.append(regex_us)

        llm_res = classify_llm(case["query"], context=ctx)
        llm_dec = llm_res.decision
        if llm_res.fallback_reason:
            n_fallback += 1
        else:
            llm_latencies.append(llm_res.elapsed_ms)
        total_cost += llm_res.cost_usd

        expected = case["expected"]
        row = {
            "id": case["id"],
            "query": case["query"],
            "expected": expected,
            "regex_intent": regex_dec.intent,
            "regex_reason": regex_dec.reason,
            "regex_ok": regex_dec.intent in expected,
            "regex_us": round(regex_us, 1),
            "llm_intent": llm_dec.intent,
            "llm_reason": llm_dec.reason,
            "llm_ok": llm_dec.intent in expected,
            "llm_ms": llm_res.elapsed_ms,
            "llm_citation": (
                {"law": llm_dec.citation.law_number, "article": llm_dec.citation.article_number}
                if llm_dec.citation else None
            ),
            "llm_fallback": llm_res.fallback_reason,
            "llm_cached_tokens": llm_res.cached_tokens,
            "llm_prompt_tokens": llm_res.prompt_tokens,
            "agree": regex_dec.intent == llm_dec.intent,
        }
        rows.append(row)
        mark_r = "✓" if row["regex_ok"] else "✗"
        mark_l = "✓" if row["llm_ok"] else "✗"
        print(f"[{i+1:>3}/{len(cases)}] {case['id']:<5} regex={regex_dec.intent:<18}{mark_r} "
              f"llm={llm_dec.intent:<18}{mark_l} {llm_res.elapsed_ms:>5}ms  {case['query'][:60]}")

    # ---- summary -----------------------------------------------------------
    n = len(rows)
    regex_acc = sum(r["regex_ok"] for r in rows) / n
    llm_acc = sum(r["llm_ok"] for r in rows) / n
    agree = sum(r["agree"] for r in rows) / n

    warm = llm_latencies[1:] if len(llm_latencies) > 1 else llm_latencies
    cold_ms = llm_latencies[0] if llm_latencies else None

    # Deployment-mode projection: fallback mode pays the LLM only on regex
    # default_fallthrough; llm-first pays it on every query.
    fallthrough = [r for r in rows if r["regex_reason"] == "default_fallthrough"]
    warm_mean = statistics.mean(warm) if warm else 0.0
    proj_llm_first = warm_mean
    proj_fallback = warm_mean * len(fallthrough) / n if n else 0.0

    print("\n" + "=" * 78)
    print(f"Queries: {n}   agreement regex↔llm: {agree:.0%}")
    print(f"Intent accuracy:  regex {regex_acc:.0%}   llm {llm_acc:.0%}")
    print(f"Regex latency:    mean {statistics.mean(regex_latencies_us):.0f}µs (negligible)")
    if llm_latencies:
        print(f"LLM latency:      cold(first) {cold_ms}ms | warm mean {warm_mean:.0f}ms "
              f"p50 {pct(warm, 50):.0f}ms p95 {pct(warm, 95):.0f}ms max {max(warm):.0f}ms")
    print(f"LLM fallbacks (errors): {n_fallback}")
    cache_hits = [r["llm_cached_tokens"] for r in rows if r["llm_prompt_tokens"]]
    if cache_hits:
        print(f"Prompt cache:     hit tokens mean {statistics.mean(cache_hits):.0f} "
              f"/ prompt {statistics.mean([r['llm_prompt_tokens'] for r in rows if r['llm_prompt_tokens']]):.0f}")
    print(f"Cost:             ${total_cost:.5f} total, ${total_cost / n:.6f}/query")
    print(f"\nProjected added routing latency per query:")
    print(f"  llm-first mode:  +{proj_llm_first:.0f}ms on EVERY query")
    print(f"  fallback mode:   +{proj_fallback:.0f}ms average "
          f"({len(fallthrough)}/{n} queries hit default_fallthrough)")

    disagreements = [r for r in rows if not r["agree"]]
    if disagreements:
        print(f"\nDisagreements ({len(disagreements)}):")
        for r in disagreements:
            better = "LLM" if r["llm_ok"] and not r["regex_ok"] else (
                "REGEX" if r["regex_ok"] and not r["llm_ok"] else "tie")
            print(f"  [{better:^5}] {r['id']:<5} regex={r['regex_intent']} ({r['regex_reason']}) "
                  f"vs llm={r['llm_intent']} ({r['llm_reason']})")
            print(f"          {r['query']}")

    out = args.out or (EVAL_DIR / "results" / f"router_llm_bench_{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n": n,
        "regex_accuracy": regex_acc,
        "llm_accuracy": llm_acc,
        "agreement": agree,
        "llm_latency_ms": {
            "cold": cold_ms,
            "warm_mean": warm_mean,
            "p50": pct(warm, 50),
            "p95": pct(warm, 95),
        },
        "projected_overhead_ms": {"llm_first": proj_llm_first, "fallback": proj_fallback},
        "total_cost_usd": total_cost,
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    print(f"\nResults written to {out}")


if __name__ == "__main__":
    main()
