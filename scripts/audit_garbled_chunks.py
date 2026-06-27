"""
Audit the corpus for GARBLED text — chunks whose PDF text layer extracted
corrupt, so Albanian letters (ë, ç) come out as `€`, digits, or `{}` (e.g.
"Kosovës" -> "Kosov6s", "për" -> "p€r").

Root cause (confirmed on 08/L-069): a subset of gazette PDFs reference a
NON-embedded font (e.g. Helvetica Type1) with a custom/broken encoding and no
ToUnicode CMap. The PDF renders fine visually, but the extractable text layer
is wrong — PyMuPDF (and any text extractor) maps glyph codes to the wrong
Unicode. Mostly newer InDesign-exported PDFs (07/L-, 08/L- series + the Customs
Code). The fix is OCR (render page -> image -> Gemini/Tesseract), which reads
the visible glyphs and ignores the broken text layer.

This script produces the affected-law list that drives the OCR re-extraction.

Run:
    cd backend && venv/bin/python scripts/audit_garbled_chunks.py
    cd backend && venv/bin/python scripts/audit_garbled_chunks.py --threshold 0.05 --json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.v2_chunker import chunk_law  # noqa: E402

LAWS_DIR = BACKEND_ROOT.parent / "Scraping" / "data" / "laws"


# A digit substituted for a letter INSIDE a word — the signature of the broken
# font ("Kosovës" -> "Kosov6s", "për" -> "p6r"). Requiring a letter on BOTH sides
# is what separates real garble from clean tokens that merely mix letters+digits:
# list items glued to the next word ("1.Në"), measurements ("B=3m", "11km"), and
# article refs ("Neni15") all keep the digit at a word edge or after "=", so they
# no longer false-positive (verified: 05_L-040, 06_L-020 -> 0 garbled chunks while
# 08_L-069/07_L-031/08_L-148 keep theirs).
_INWORD_DIGIT = re.compile(r"[^\W\d_]\d[^\W\d_]", re.UNICODE)


def is_corrupt_token(t: str) -> bool:
    if any(c in t for c in "€{}|"):  # ë/ç -> €, or OCR-only gibberish punctuation
        return True
    if "/" in t or "-" in t:  # law numbers / codes legitimately mix digits+letters
        return False
    return bool(_INWORD_DIGIT.search(t))


def garble_ratio(text: str) -> float:
    words = [t for t in text.split() if any(c.isalpha() for c in t)]
    if len(words) < 15:
        return 0.0
    return sum(is_corrupt_token(t) for t in words) / len(words)


def _extract(pdf: Path) -> str:
    import fitz
    doc = fitz.open(str(pdf))
    try:
        return "\n".join(p.get_text("text") for p in doc)
    finally:
        doc.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.05, help="garble ratio above which a chunk counts as garbled")
    ap.add_argument("--json", action="store_true", help="emit the affected-law list as JSON for tooling")
    args = ap.parse_args()

    dirs = sorted(d for d in LAWS_DIR.iterdir() if d.is_dir())
    total_chunks = 0
    garbled_chunks = 0
    by_law: collections.Counter[str] = collections.Counter()

    for d in dirs:
        pdfs = sorted(d.glob("*.pdf"))
        if not pdfs:
            continue
        try:
            txt = "\n".join(_extract(p) for p in pdfs)
        except Exception:
            continue
        if len(txt.strip()) < 500:  # scanned/empty — skipped by ingest anyway
            continue
        law = d.name.replace("_", "/", 1)
        for ch in chunk_law(law, txt):
            total_chunks += 1
            if garble_ratio(ch.content) > args.threshold:
                garbled_chunks += 1
                by_law[law] += 1

    affected = dict(by_law.most_common())
    if args.json:
        print(json.dumps({
            "threshold": args.threshold,
            "total_chunks": total_chunks,
            "garbled_chunks": garbled_chunks,
            "affected_laws": affected,
        }, ensure_ascii=False, indent=2))
        return

    print(f"threshold: >{args.threshold:.0%} garbled tokens per chunk")
    print(f"total chunks: {total_chunks}")
    print(f"garbled chunks: {garbled_chunks} ({100*garbled_chunks/max(total_chunks,1):.2f}%)")
    print(f"affected laws: {len(affected)}  (re-extract these via OCR)")
    for law, n in affected.items():
        print(f"  {law:<20} {n}")


if __name__ == "__main__":
    main()
