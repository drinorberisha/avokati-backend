"""
Batch OCR re-extraction of garbled laws (see scripts/audit_garbled_chunks.py).

For each affected law it runs the GATED OCR re-ingest (`reingest_law.py --ocr`)
and records the outcome to a resumable JSONL report, so the laws that DON'T pass
the gate (still garbled, dup ids, lost articles, OCR/network error) are captured
for manual handling rather than silently skipped.

Resumable: a law already in the report is skipped on re-run. Bounded for cost:
--max-pages defers the big codes, --limit caps the run.

Run:
    cd backend && venv/bin/python scripts/ocr_reextract_batch.py --max-pages 25 --limit 8
    cd backend && venv/bin/python scripts/ocr_reextract_batch.py --report-only   # just print the tally
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import fitz  # noqa: E402
from app.ai.v2_chunker import chunk_law  # noqa: E402
from scripts.audit_garbled_chunks import garble_ratio  # noqa: E402
from scripts.build_v2_index import LAWS_DIR, _law_number_from_dirname  # noqa: E402

REPORT = BACKEND_ROOT / "tests" / "eval" / "results" / "ocr_reextract_report.jsonl"


def _affected(threshold: float, min_chunks: int) -> list[dict]:
    out: list[dict] = []
    for d in sorted(p for p in LAWS_DIR.iterdir() if p.is_dir()):
        pdfs = sorted(d.glob("*.pdf"))
        if not pdfs:
            continue
        try:
            docs = [fitz.open(str(p)) for p in pdfs]
            txt = "\n".join("\n".join(pg.get_text("text") for pg in doc) for doc in docs)
            pages = sum(doc.page_count for doc in docs)
            for doc in docs:
                doc.close()
        except Exception:
            continue
        if len(txt.strip()) < 500:
            continue
        n_bad = sum(1 for c in chunk_law(_law_number_from_dirname(d.name), txt) if garble_ratio(c.content) > threshold)
        if n_bad >= min_chunks:
            out.append({"dir": d.name, "garbled_chunks": n_bad, "pages": pages})
    return out


def _load_done() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if REPORT.exists():
        for line in REPORT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["dir"]] = r
    return done


def _classify(out: str, code: int) -> tuple[str, str]:
    if "upserted" in out and "GATE FAILED" not in out:
        return "INGESTED", out.split("upserted", 1)[1].strip().split("\n")[0]
    if "GATE FAILED" in out:
        return "GATE_FAILED", out.split("GATE FAILED", 1)[1].strip().split("\n")[0]
    return "ERROR", (out.strip().splitlines() or ["<no output>"])[-1][:160]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.05)
    ap.add_argument("--min-chunks", type=int, default=1, help="min garbled chunks for a law to be in scope")
    ap.add_argument("--max-pages", type=int, default=10**9, help="skip laws with more pages than this (defer big codes)")
    ap.add_argument("--limit", type=int, default=10**9, help="process at most this many laws this run")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    affected = sorted(_affected(args.threshold, args.min_chunks), key=lambda r: r["pages"])
    done = _load_done()

    if args.report_only:
        _summary(affected, done)
        return

    # Retry transient ERRORs (network/DNS); leave INGESTED and GATE_FAILED alone
    # (gate failures need manual review, not an auto-retry).
    todo = [
        a for a in affected
        if (a["dir"] not in done or done[a["dir"]]["status"] == "ERROR")
        and a["pages"] <= args.max_pages
    ][: args.limit]
    print(f"affected laws: {len(affected)} | already done: {len(done)} | this run: {len(todo)} "
          f"(deferred >{args.max_pages}p: {sum(1 for a in affected if a['pages']>args.max_pages and a['dir'] not in done)})\n")

    with REPORT.open("a", encoding="utf-8") as fh:
        for a in todo:
            t0 = time.time()
            print(f"  [{a['dir']}] {a['pages']}p, {a['garbled_chunks']} garbled chunks — OCR…", flush=True)
            try:
                proc = subprocess.run(
                    [sys.executable, str(BACKEND_ROOT / "scripts" / "reingest_law.py"), "--law", a["dir"], "--ocr"],
                    capture_output=True, text=True, timeout=3600,
                )
                status, detail = _classify(proc.stdout + proc.stderr, proc.returncode)
            except subprocess.TimeoutExpired:
                status, detail = "ERROR", "timeout (>3600s)"
            rec = {**a, "status": status, "detail": detail, "secs": round(time.time() - t0)}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"     → {status}  ({rec['secs']}s)  {detail[:80]}")

    _summary(affected, _load_done())


def _summary(affected: list[dict], done: dict[str, dict]) -> None:
    from collections import Counter
    c = Counter(r["status"] for r in done.values())
    print("\n=== OCR re-extraction tally ===")
    print(f"  in scope: {len(affected)} laws | processed: {len(done)}")
    for st in ("INGESTED", "GATE_FAILED", "ERROR"):
        print(f"  {st}: {c.get(st,0)}")
    not_passed = [r for r in done.values() if r["status"] != "INGESTED"]
    if not_passed:
        print("\n  NOT INGESTED (handle manually):")
        for r in sorted(not_passed, key=lambda r: r["dir"]):
            print(f"    {r['dir']:<22} {r['status']:<12} {r.get('detail','')[:70]}")
    print(f"\n  report: {REPORT}")


if __name__ == "__main__":
    main()
