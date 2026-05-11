"""Quick sanity tests for the citation parser. Run as a script, not via pytest."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.citation import parse_citation, canonicalize_law_number, law_number_variants


CASES = [
    # (query, expected_canonical_law, expected_article)
    ("Çfarë thotë Neni 5 i Ligjit 04/L-079?", "04/L-079", "5"),
    ("Çfarë përcakton Neni 13 i ligjit 03/L-094?", "03/L-094", "13"),
    ("Sipas Ligjit 04/L-125, Neni 4, çfarë rregullohet?", "04/L-125", "4"),
    ("A është aktiv Ligji 04/L-226?", "04/L-226", None),
    ("A është abroguar Ligji 2004/2?", "2004/2", None),
    ("Cili ligj e ka zëvendësuar Ligjin 03/L-018?", "03/L-018", None),
    ("Më trego përmbajtjen e Nenit 121 të Ligjit KUV-08/L-032-KOD.", "KUV-08/L-032-KOD", "121"),
    ("Qfar thote ligji 05/L-065?", "05/L-065", None),
    # Pure topic queries should NOT match
    ("Cilat ligje rregullojnë grantet?", None, None),
    ("Çfarë thotë legjislacioni për shoqatave?", None, None),
]

passed = 0
failed = 0
for query, exp_law, exp_art in CASES:
    c = parse_citation(query)
    if exp_law is None:
        ok = c is None
    else:
        ok = c is not None and c.law_number == exp_law and c.article_number == exp_art
    marker = "✓" if ok else "✗"
    print(f"  {marker} {query[:80]}")
    if not ok:
        print(f"      expected=({exp_law!r}, {exp_art!r}) got={c!r}")
        failed += 1
    else:
        passed += 1

print(f"\n{passed} passed, {failed} failed")

print("\n--- variants ---")
for law in ["04/L-079", "03/L-094", "2004/2", "KUV-08/L-032-KOD"]:
    print(f"  {law!r} → {law_number_variants(law)}")

if failed:
    sys.exit(1)
