"""
Regression test suite for Phase 2 + Phase 3 modules.

Runs as a plain Python script (no pytest dependency to keep the eval
toolchain self-contained). Returns nonzero on any failure so CI can wire
it directly. Each test is small and targeted at one behavior — when one
breaks, you should be able to point at the cause without a binary search.

Run:
    cd backend && venv/bin/python tests/eval/test_phase23_modules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

# These tests don't talk to Pinecone or OpenAI — they exercise pure logic.
from app.ai.bm25_rescore import rescore as bm25_rescore, tokenize  # noqa: E402
from app.ai.citation import (  # noqa: E402
    Citation,
    canonicalize_law_number,
    chunk_starts_article,
    law_number_variants,
    parse_citation,
)
from app.ai.citation_validator import (  # noqa: E402
    annotate_unverified,
    extract_citations,
    summarize as cv_summarize,
    validate_against_sources,
)
from app.ai.router import classify  # noqa: E402
from app.ai.v2_adapter import adapt_pipeline_result_to_v2, score_to_band  # noqa: E402
from app.ai.v2_chunker import chunk_law  # noqa: E402


# ----- helpers -----------------------------------------------------------

_failures: list[tuple[str, str]] = []


def expect(name: str, condition: bool, detail: str = "") -> None:
    """Mini-assertion that records failures rather than raising."""
    if condition:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}: {detail}")
        _failures.append((name, detail))


# ----- citation parser ---------------------------------------------------

def test_citation_parser() -> None:
    print("\n[citation parser]")

    cases = [
        ("Çfarë thotë Neni 5 i Ligjit 04/L-079?", "04/L-079", "5"),
        ("Sipas Nenit 121 të Ligjit KUV-08/L-032-KOD", "KUV-08/L-032-KOD", "121"),
        ("A është aktiv Ligji 04/L-226?", "04/L-226", None),
        ("A është abroguar Ligji 2004/2?", "2004/2", None),
        ("Cilat ligje rregullojnë grantet?", None, None),
    ]
    for query, exp_law, exp_art in cases:
        c = parse_citation(query)
        if exp_law is None:
            expect(f"no-citation: {query[:40]!r}", c is None, f"got {c!r}")
        else:
            ok = c is not None and c.law_number == exp_law and c.article_number == exp_art
            expect(
                f"parse: {query[:40]!r}", ok,
                f"expected ({exp_law!r}, {exp_art!r}) got {c!r}",
            )

    # Variant generation
    variants = law_number_variants("04/L-079")
    expect(
        "variants include canonical + NR.",
        "04/L-079" in variants and "NR.04/L-079" in variants,
        f"got {variants}",
    )

    expect(
        "canonicalize strips spaces",
        canonicalize_law_number(" 04 / L - 079 ") == "04/L-079",
    )

    # chunk_starts_article — Markdown header detection
    content = "## Neni 5\nKujdesi ndaj kafshëve\nDispozitat..."
    expect("chunk_starts_article: matches", chunk_starts_article(content, "5"))
    expect("chunk_starts_article: rejects different N", not chunk_starts_article(content, "7"))


# ----- v2 chunker --------------------------------------------------------

def test_v2_chunker() -> None:
    print("\n[v2 chunker]")

    canonical = """\
Ligji Nr. 02/L-10
PËR PËRKUJDESJEN NDAJ KAFSHËVE

Kreu I
DISPOZITAT E PËRGJITHSHME

Neni 1
Lënda

Me këtë ligj rregullohet mbajtja, kujdesi, strehimi, mbarështimi, transportimi.

Neni 2
Qëllimi

Qëllimi i këtij ligji është krijimi i një baze ligjore.

Neni 3
Definicionet

Në këtë ligj termet kanë domethënie të caktuara.
"""
    chunks = chunk_law("02/L-10", canonical)
    expect("chunker: 3 articles found", len(chunks) == 3, f"got {len(chunks)}")
    expect("chunker: art1 title=Lënda", chunks[0].article_title == "Lënda")
    expect("chunker: art1 chapter=I", chunks[0].chapter_number == "I")
    expect("chunker: chunk_id format", chunks[0].chunk_id == "02_L-10_art1")

    # Inline-title format ("Neni 1 - Programi") — used by IPA/ratification laws
    # Both bodies must exceed the 50-char drop-floor or they get filtered.
    inline = """\
Ligji Nr. 07/L-031
PËR RATIFIKIMIN

Neni 1 - Programi

Unioni pajtohet që të financojë dhe përfituesi pajtohet që të pranojë financimin.

Neni 2 - Periudha e ekzekutimit

Periudha e ekzekutimit të kësaj marrëveshjeje është dymbëdhjetë vite nga data e hyrjes.
"""
    chunks2 = chunk_law("07/L-031", inline)
    expect("chunker: inline-title format detected", len(chunks2) == 2, f"got {len(chunks2)}")
    if chunks2:
        expect("chunker: inline title captured", chunks2[0].article_title == "Programi")

    # Sub-50-char articles get dropped to avoid index pollution
    short = "Neni 1\nFin\n\nNeni 2\nFin\n"
    chunks3 = chunk_law("99/L-99", short)
    expect("chunker: drops short articles", len(chunks3) == 0)


# ----- BM25 rescoring ----------------------------------------------------

def test_bm25_rescore() -> None:
    print("\n[bm25 rescore]")

    # Tokenizer
    toks = tokenize("Çfarë thotë ligji për përkujdesjen ndaj kafshëve?")
    expect("tokenize: includes content words", "kafshëve" in toks and "përkujdesjen" in toks)
    expect("tokenize: drops 'ligji' stopword", "ligji" not in toks)

    # Rescoring promotes lexical match over a stronger dense score when α=0.
    # NOTE: BM25Okapi's IDF degenerates near 0 when a term appears in
    # exactly half the corpus (df/N = 0.5). Real usage has pool=200 so
    # this is a non-issue, but the test must use a corpus large enough
    # that the IDF doesn't collapse.
    pool = [
        {"content": "tekst i përgjithshëm pa lidhje", "score": 0.95, "id": "a"},
        {"content": "kafshëve dhe përkujdesjes", "score": 0.50, "id": "b"},
        {"content": "rregulla buxhetore për institucionet", "score": 0.40, "id": "c"},
        {"content": "tatime dhe taksa komunale", "score": 0.30, "id": "d"},
        {"content": "kontratë e punës dhe orari", "score": 0.20, "id": "e"},
    ]
    pure_bm25 = bm25_rescore("kafshëve", pool, alpha=0.0, top_k=3)
    expect("bm25 α=0: lexical match wins", pure_bm25[0]["id"] == "b", f"got {pure_bm25[0]['id']}")

    # α=1.0 returns dense order unchanged
    pure_dense = bm25_rescore("kafshëve", pool, alpha=1.0, top_k=2)
    expect("bm25 α=1: dense order preserved", pure_dense[0]["id"] == "a")


# ----- router ------------------------------------------------------------

def test_router() -> None:
    print("\n[router]")

    cases = [
        ("Përshëndetje!", "greeting"),
        ("Tungjatjeta", "greeting"),
        ("Më jep një recetë për byrek.", "out_of_scope"),
        ("Çfarë thotë Neni 5 i Ligjit 02/L-10?", "citation_lookup"),
        ("A është aktiv Ligji 04/L-051?", "status_lookup"),
        ("A është abroguar Ligji 2004/2?", "status_lookup"),
        ("Cilat janë dispozitat për shoqatat?", "semantic_question"),
        ("", "greeting"),  # empty → safe default
    ]
    for q, expected in cases:
        d = classify(q)
        expect(f"route: {q[:40]!r}", d.intent == expected, f"got {d.intent}")


# ----- citation validator ------------------------------------------------

def test_citation_validator() -> None:
    print("\n[citation validator]")

    # Albanian declensions. The fake law uses digits only (the regex requires
    # them) — that's how a hallucination would actually shape a wrong number.
    text = (
        "Sipas Nenit 8 të Ligjit 02/L-10 detyrimet janë të qarta. "
        "Shih edhe Ligjin 03/L-999 për shembull. "
        "Sipas [Neni 5, Ligji 02/L-10] mbajtësi përgjigjet."
    )
    cites = extract_citations(text)
    laws = [(c.law_number, c.article_number) for c in cites]
    expect("extract: catches Nenit 8...Ligjit 02/L-10", ("02/L-10", "8") in laws, f"got {laws}")
    expect("extract: catches bare Ligjin 03/L-999", any(c.law_number == "03/L-999" for c in cites))
    expect("extract: catches bracketed [Neni 5, Ligji 02/L-10]", ("02/L-10", "5") in laws)

    sources = [
        {"metadata": {"law_number": "02/L-10", "article_number": "5", "chunk_id": "02_L-10_art5"}, "content": "## Neni 5"},
        {"metadata": {"law_number": "02/L-10", "article_number": "8", "chunk_id": "02_L-10_art8"}, "content": "## Neni 8"},
    ]
    validated = validate_against_sources(cites, sources)
    verified = {(v.citation.law_number, v.citation.article_number): v.verified for v in validated}
    expect("validate: real (02/L-10, 5) verified", verified.get(("02/L-10", "5")) is True)
    expect("validate: real (02/L-10, 8) verified", verified.get(("02/L-10", "8")) is True)
    expect("validate: fake (03/L-999, None) NOT verified", verified.get(("03/L-999", None)) is False)

    annotated = annotate_unverified(text, validated)
    expect("annotate: marker on fake", "i paverifikuar" in annotated)
    expect("annotate: not on real", annotated.count("i paverifikuar") == 1)

    summary = cv_summarize(validated)
    expect(
        "summary: counts match",
        summary["citations_total"] == 3 and summary["citations_verified"] == 2,
        f"got {summary}",
    )


# ----- main --------------------------------------------------------------

def test_v2_adapter() -> None:
    print("\n[v2 adapter]")

    # Score banding — covers the four output bands plus the synthetic-chunk
    # carve-out used for status_lookup / abolishment chunks.
    expect("band: strong on high score", score_to_band(0.6, "article") == "strong")
    expect("band: moderate on mid score", score_to_band(0.4, "article") == "moderate")
    expect("band: topical on low-relevant", score_to_band(0.22, "article") == "topical")
    expect("band: weak on very low", score_to_band(0.05, "article") == "weak")
    expect(
        "band: synthetic verdict always strong",
        score_to_band(0.0, "status_verdict") == "strong",
    )

    # End-to-end adapter on a hand-built AnswerResult — does NOT hit
    # Pinecone / OpenAI. Uses an empty registry/catalog so we don't
    # accidentally couple the test to on-disk data.
    from app.ai.law_catalog import LawCatalog
    from app.ai.abolishment import AbolishmentRegistry
    from app.ai.pipeline import AnswerResult

    catalog = LawCatalog(path=Path("/nonexistent"))   # empty
    registry = AbolishmentRegistry(path=Path("/nonexistent"))  # empty

    fake = AnswerResult(
        query="Çfarë thotë Neni 5 i Ligjit 02/L-10?",
        intent="citation_lookup",
        answer="Sipas dispozitave [Neni 5, Ligji 02/L-10] mbajtësi përgjigjet.",
        sources=[
            {
                "id": "02_L-10_art5",
                "metadata": {
                    "law_number": "02/L-10",
                    "article_number": "5",
                    "article_title": "Kujdesi ndaj kafshëve",
                    "chunk_type": "article",
                },
                "score": 1.0,
                "content": "## Neni 5\nKujdesi ndaj kafshëve\n5.1. ...",
            },
            {
                "id": "02_L-10_art4",
                "metadata": {
                    "law_number": "02/L-10",
                    "article_number": "4",
                    "article_title": "Mbajtja e kafshëve",
                    "chunk_type": "article",
                },
                "score": 0.9,
                "content": "## Neni 4\nMbajtja e kafshëve\n4.1. ...",
            },
        ],
        citations=[{
            "raw": "[Neni 5, Ligji 02/L-10]",
            "law_number": "02/L-10",
            "article_number": "5",
            "verified": True,
            "matched_source_id": "02_L-10_art5",
        }],
        abolishment_warnings=[],
        route_trace={"intent": "citation_lookup", "citation_match_quality": "exact_start"},
        elapsed_ms=120,
    )

    out = adapt_pipeline_result_to_v2(fake, catalog=catalog, registry=registry)
    expect("adapter: 2 sources passed through", len(out.sources) == 2)
    expect(
        "adapter: primary flag on asked-for article",
        out.sources[0].is_primary is True and out.sources[1].is_primary is False,
    )
    expect(
        "adapter: cited flag set from validated citations",
        out.sources[0].is_cited is True,
    )
    expect(
        "adapter: score_band derived from score+chunk_type",
        out.sources[0].score_band == "strong",
    )
    expect(
        "adapter: citations passed through with verified flag",
        len(out.citations) == 1 and out.citations[0].verified is True,
    )
    expect("adapter: elapsed_ms preserved", out.elapsed_ms == 120)


def main() -> None:
    test_citation_parser()
    test_v2_chunker()
    test_bm25_rescore()
    test_router()
    test_citation_validator()
    test_v2_adapter()

    print()
    if _failures:
        print(f"FAILED ({len(_failures)} failures):")
        for name, detail in _failures:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
