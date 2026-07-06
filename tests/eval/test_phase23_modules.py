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

    # Tokenizer (now diacritic-folded — tokens come back folded, e.g. kafshëve→kafsheve)
    toks = tokenize("Çfarë thotë ligji për përkujdesjen ndaj kafshëve?")
    expect("tokenize: includes content words (folded)", "kafsheve" in toks and "perkujdesjen" in toks)
    expect("tokenize: drops 'ligji' stopword", "ligji" not in toks)
    # G7: diacritic and diacritic-free variants collapse to the same tokens
    expect("tokenize: diacritic variants unify",
           tokenize("çështje") == tokenize("ceshtje") == ["ceshtje"],
           f"got {tokenize('çështje')} vs {tokenize('ceshtje')}")
    expect("tokenize: dëmshpërblim ≡ demshperblim",
           tokenize("dëmshpërblim") == tokenize("demshperblim"))
    expect("tokenize: folded stopword still dropped", "eshte" not in tokenize("është kontrata"))

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


def test_partial_turn_content() -> None:
    print("\n[partial turn content (G4)]")
    from app.api.api_v1.endpoints.legal_ai import _partial_turn_content

    partial = _partial_turn_content("Neni 80 përcakton dëmshpërblimin", "stream_timeout")
    expect("partial: keeps streamed text", "Neni 80 përcakton dëmshpërblimin" in partial)
    expect("partial: has interrupted marker", "u ndërpre" in partial)

    empty = _partial_turn_content("", "pipeline_error")
    expect("empty: non-empty placeholder (NOT NULL safe)", len(empty.strip()) > 0)
    expect("empty: no double marker", "u ndërpre" not in empty)
    expect("whitespace-only treated as empty", _partial_turn_content("   \n ", "x") == empty)


def test_text_norm() -> None:
    print("\n[text norm]")
    from app.ai.text_norm import fold
    from app.ai.abolishment import is_status_query

    expect("fold: çështje → ceshtje", fold("çështje") == "ceshtje")
    expect("fold: DËMSHPËRBLIM → demshperblim", fold("DËMSHPËRBLIM") == "demshperblim")
    expect("fold: ascii unchanged", fold("kontrata 04/L-077") == "kontrata 04/l-077")
    expect("fold: length preserved", len(fold("çështje")) == len("çështje"))

    # status keyword gap closed (valid); is_status_query only consulted when a law is
    # named, so this is safe against non-status "validim" text.
    expect("status: 'valid' recognized", is_status_query("a është valid ky ligj"))
    expect("status: 'vlen' recognized", is_status_query("a vlen ky ligj"))
    expect("status: plain topical → False", not is_status_query("cilat janë kushtet"))


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
        # --- clarify: incomplete law reference (bare/partial term-prefix) ---
        ("neni 47 i ligjit 04", "clarify"),
        ("Çfarë thotë ligji 4?", "clarify"),
        ("ligji nr 05 neni 12", "clarify"),
        ("ligji 04/L", "clarify"),  # dangling /L, no number
        # --- clarify: article referenced with no law ("of which law?") ---
        ("neni 37", "clarify"),
        ("Neni 5", "clarify"),
        ("neni 1059", "clarify"),
        ("çfarë thotë neni 37?", "clarify"),
        ("më trego nenin 12", "clarify"),
        ("neni 5 paragrafi 2", "clarify"),
        # informal Kosovo / diacritic-free wrappers around a bare article → clarify
        ("qfare thote neni 47", "clarify"),
        ("qka thote neni 47", "clarify"),
        ("qfar thot neni 47", "clarify"),
        ("qysh e rregullon neni 47", "clarify"),
        ("shka thote neni 47", "clarify"),
        ("cfare percakton neni 12", "clarify"),
        ("me trego neni 5", "clarify"),
        # --- precision bar: these must NOT be treated as incomplete ---
        ("Neni 47 i Ligjit 04/L-077", "citation_lookup"),   # complete number
        ("nenin 24 të Kushtetutës", "citation_lookup"),      # named law
        ("A ka ndryshuar ligji në 2020?", "semantic_question"),
        ("Cilat janë dispozitat e ligjit të punës?", "semantic_question"),
        # article ref + real topical content → semantic (net catches failures)
        ("çfarë thotë neni 37 për pushimet vjetore", "semantic_question"),
        ("si parashkruhet padia sipas nenit 37", "semantic_question"),
        # informal wrapper BUT with a real topical noun → still semantic (not hijacked)
        ("qfare thote neni 47 per trashegimine", "semantic_question"),
        ("qysh e rregullon neni 47 punen jashte orarit", "semantic_question"),
        ("neni 47 per pushimet vjetore", "semantic_question"),
        ("", "greeting"),  # empty → safe default
    ]
    for q, expected in cases:
        d = classify(q)
        expect(f"route: {q[:40]!r}", d.intent == expected, f"got {d.intent}")


def test_relevance_gate() -> None:
    print("\n[relevance gate]")
    import importlib
    from app.ai import pipeline as pl

    short = "çfarë thotë ligji"  # < 300 chars, semantic-style

    def src(**scores):
        return [{"id": "x", "metadata": {}, "content": "c", **scores}]

    # Trusted rerank tier
    g, tier, top = pl._relevance_gate(short, src(_rerank_score=0.80))
    expect("rerank strong → not gated", not g and tier == "rerank")
    g, tier, top = pl._relevance_gate(short, src(_rerank_score=0.10))
    expect("rerank weak → gated", g and tier == "rerank")

    # Reranker absent → dense fallback (the G1 case)
    g, tier, top = pl._relevance_gate(short, src(_dense_score=0.55))
    expect("no rerank, strong dense → not gated", not g and tier == "dense")
    g, tier, top = pl._relevance_gate(short, src(_dense_score=0.10))
    expect("no rerank, weak dense → gated (dense)", g and tier == "dense")

    # No trusted signal at all → fail-closed
    g, tier, top = pl._relevance_gate(short, src(score=0.99))  # pool-relative only
    expect("no rerank/dense → gated (no_signal, fail-closed)", g and tier == "no_signal")

    # Exemptions preserved
    long_q = "x" * (pl.RELEVANCE_FLOOR_MAX_QUERY_LEN + 1)
    g, tier, top = pl._relevance_gate(long_q, src(_rerank_score=0.01))
    expect("long query → exempt (not gated)", not g and tier == "exempt_long")
    g, tier, top = pl._relevance_gate(short, [])
    expect("no sources → exempt (not gated)", not g and tier == "exempt_disabled")

    # Back-compat boolean wrapper still agrees with the rerank path
    expect("_below_relevance_floor wrapper: weak rerank → True",
           pl._below_relevance_floor(short, src(_rerank_score=0.10)) is True)
    expect("_below_relevance_floor wrapper: strong rerank → False",
           pl._below_relevance_floor(short, src(_rerank_score=0.80)) is False)


def test_long_query_gate() -> None:
    print("\n[long query gate]")
    from app.ai import pipeline as pl

    # _split_query_parts: multi-sentence → substantive parts; trivia dropped
    q_multi = ("Padia u ngrit për dëmshpërblim. A parashkruhet ajo pas tre vjetësh? "
               "Kush e mban barrën e provës në kontestet civile?")
    expect("split: 3 substantive parts", len(pl._split_query_parts(q_multi)) == 3,
           f"got {pl._split_query_parts(q_multi)}")
    expect("split: drops trivial fragments", pl._split_query_parts("Faleminderit. Po.") == [])

    # a > cap multi-sentence fact pattern (kazus)
    long_q = (
        "Klienti im pati një aksident trafiku në Prishtinë ku u dëmtua automjeti i tij. "
        "Pala tjetër nuk kishte siguracion valid në kohën e aksidentit. "
        "A mund të kërkojë dëmshpërblim nga fondi i kompensimit dhe brenda cilit afat? "
        "A ndikon neglizhenca e pjesshme e klientit tim në shumën e dëmshpërblimit? "
        "Cilat dokumente duhet të paraqiten për të vërtetuar dëmin e pësuar?"
    )
    expect("long_q exceeds cap", len(long_q) > pl.RELEVANCE_FLOOR_MAX_QUERY_LEN)

    orig = pl._semantic_retrieve
    calls = {"n": 0}
    try:
        # every part below floor → refuse
        def weak(part, ns):
            calls["n"] += 1
            return [{"_rerank_score": 0.05, "metadata": {}}]
        pl._semantic_retrieve = weak
        g, tier, top = pl._long_query_gate(long_q, "default_v2")
        expect("all parts below floor → gated", g and tier == "decomposed_all_below",
               f"got {g},{tier}")

        # first part clears the floor → answer AND short-circuit (1 retrieval)
        calls["n"] = 0
        def first_strong(part, ns):
            calls["n"] += 1
            return [{"_rerank_score": 0.9 if "aksident" in part else 0.05, "metadata": {}}]
        pl._semantic_retrieve = first_strong
        g, tier, top = pl._long_query_gate(long_q, "default_v2")
        expect("a part clears → not gated (decomposed_pass)", (not g) and tier == "decomposed_pass")
        expect("short-circuits on first passing part", calls["n"] == 1, f"queried {calls['n']}")

        # retrieval error → fail-open (never refuse a real kazus on a glitch)
        def boom(part, ns):
            raise RuntimeError("pinecone down")
        pl._semantic_retrieve = boom
        g, tier, top = pl._long_query_gate(long_q, "default_v2")
        expect("retrieval error → fail-open", (not g) and tier == "exempt_long_error")
    finally:
        pl._semantic_retrieve = orig

    # single run-on (no sentence breaks) longer than the cap → can't decompose → exempt
    runon = "Klienti im kishte një problem juridik shumë të komplikuar dhe të gjatë " * 6
    g, tier, top = pl._long_query_gate(runon.strip(), "default_v2")
    expect("run-on longer than cap → exempt_long", (not g) and tier == "exempt_long", f"got {tier}")


def test_multilaw_gate() -> None:
    print("\n[multilaw gate]")
    from app.ai import pipeline as pl

    def art47(law):
        return {"id": f"{law}_a47", "metadata": {"law_number": law, "article_number": "47"}, "content": "c"}

    # bare "neni 47", art 47 across 3 distinct laws → gated
    srcs3 = [art47("2004/26"), art47("02/L-8"), art47("2004/18")]
    g, n = pl._multilaw_ambiguous("çfarë thotë neni 47", srcs3)
    expect("3 distinct laws → gated", g and n == 3)

    # only 2 distinct laws → not gated
    g, n = pl._multilaw_ambiguous("çfarë thotë neni 47", srcs3[:2])
    expect("2 distinct laws → not gated", (not g) and n == 2)

    # multi-part chunks of ONE law for art 47 → counts as 1 law
    onelaw = [art47("2004/26"), {"id": "2004/26_a47_p2", "metadata": {"law_number": "2004/26", "article_number": "47"}, "content": "c"}]
    g, n = pl._multilaw_ambiguous("neni 47", onelaw)
    expect("multi-part one law → 1 distinct", (not g) and n == 1)

    # query names its own law → never ambiguous (even with many art-47 sources)
    g, n = pl._multilaw_ambiguous("neni 47 i ligjit 04/L-077", srcs3)
    expect("query names a law → not gated", (not g) and n == 0)

    # query has no article ref → not gated
    g, n = pl._multilaw_ambiguous("Cilat janë dispozitat për shoqatat?", srcs3)
    expect("no article ref → not gated", (not g) and n == 0)

    # only sources whose article_number matches the queried N count
    mixed = [art47("2004/26"), {"id": "x_a5", "metadata": {"law_number": "02/L-8", "article_number": "5"}, "content": "c"}, art47("2004/18")]
    g, n = pl._multilaw_ambiguous("neni 47", mixed)
    expect("only matching-article sources counted", (not g) and n == 2)


def test_conversation() -> None:
    print("\n[conversation follow-ups]")
    from app.ai.conversation import derive_context, resolve_followup
    from app.ai.router import classify

    history = [
        {"role": "user", "content": "Neni 37 i Ligjit 03/L-212"},
        {"role": "assistant", "content": (
            "Neni 37 i Ligjit 03/L-212 rregullon pushimin vjetor "
            "[Neni 37, par. 1, Ligji 03/L-212] ... [Neni 37, par. 6, Ligji 03/L-212]."
        )},
    ]
    ctx = derive_context(history)
    expect("derive: focus law", ctx.focus_law == "03/L-212", f"got {ctx.focus_law}")
    expect("derive: focus article", ctx.focus_article == "37", f"got {ctx.focus_article}")
    expect("derive: empty history → no focus", not derive_context([]).has_focus)

    # resolve_followup inherits the right (law, article)
    def inh(q):
        return resolve_followup(q, ctx)
    expect("resolve: paragraph deepen inherits focus",
           (c := inh("A mund te thellohesh tek Paragrafi 1 i ketij neni")) is not None
           and c.law_number == "03/L-212" and c.article_number == "37")
    expect("resolve: bare 'neni 38' inherits law, new article",
           (c := inh("neni 38")) is not None
           and c.law_number == "03/L-212" and c.article_number == "38")
    expect("resolve: 'shpjego më shumë' inherits focus",
           (c := inh("shpjego më shumë")) is not None and c.article_number == "37")
    expect("resolve: 'po paragrafi 2?' inherits focus",
           (c := inh("po paragrafi 2?")) is not None and c.article_number == "37")
    expect("resolve: new question → None",
           inh("Cilat janë kushtet për themelimin e OJQ-së?") is None)
    expect("resolve: own citation → None",
           inh("Neni 5 i Ligjit 02/L-10") is None)
    expect("resolve: no focus → None",
           resolve_followup("shpjego më shumë", derive_context([])) is None)

    # end-to-end routing with context
    route_cases = [
        ("A mund te thellohesh tek Paragrafi 1 i ketij neni", "citation_lookup"),
        ("neni 38", "citation_lookup"),
        ("a vlen ende ky ligj?", "status_lookup"),
        ("Cilat janë kushtet për themelimin e OJQ-së?", "semantic_question"),
    ]
    for q, exp in route_cases:
        d = classify(q, context=ctx)
        expect(f"route+ctx: {q[:34]!r}", d.intent == exp, f"got {d.intent}")

    # regression: a canned clarify/greeting message's EXAMPLE citation must not
    # become the focus (bug: "neni 5" after a clarify inherited the example 03/L-212)
    from app.ai.router import CLARIFY_MISSING_LAW_RESPONSE, GREETING_RESPONSE
    clarify_hist = [
        {"role": "user", "content": "neni 37"},
        {"role": "assistant", "content": CLARIFY_MISSING_LAW_RESPONSE},
    ]
    expect("clarify example not taken as focus", not derive_context(clarify_hist).has_focus)
    expect("after clarify, bare article still clarifies",
           classify("çfarë thotë neni 5?", context=derive_context(clarify_hist)).intent == "clarify")
    expect("greeting example not taken as focus",
           not derive_context([{"role": "assistant", "content": GREETING_RESPONSE}]).has_focus)

    # regression: without context, behavior is unchanged (stateless)
    expect("no-ctx 'neni 38' → clarify", classify("neni 38").intent == "clarify")
    expect("no-ctx follow-up → semantic",
           classify("A mund te thellohesh tek Paragrafi 1 i ketij neni").intent
           == "semantic_question")


def test_named_law_resolver() -> None:
    print("\n[named-law resolver]")
    from app.ai.citation import parse_citation, law_number_variants
    from app.ai.router import classify

    # name/abbrev + article → resolved citation with the verified number
    resolves = [
        ("neni 5 i Ligjit të Punës", "03/L-212", "5"),
        ("neni 47 i LMD-së", "04/L-077", "47"),
        ("neni 12 i LPK", "03/L-006", "12"),
        ("neni 5 i Kodit të Procedurës Penale", "KUV-08/L-032-KOD", "5"),
        ("neni 10 i Kodit Penal", "KUV-06/L-074-KOD", "10"),   # NOT the procedure code
        ("çfarë thotë neni 8 i Ligjit për Familjen", "2004/32", "8"),
        ("neni 47 i Ligjit për Trashëgiminë", "2004/26", "47"),
    ]
    for q, law, art in resolves:
        c = parse_citation(q)
        expect(f"resolve: {q[:34]!r}",
               c is not None and c.law_number == law and c.article_number == art and c.by_name,
               f"got {c}")
        expect(f"route: {q[:30]!r} → citation_lookup", classify(q).intent == "citation_lookup")

    # status keyword on a named law → status_lookup
    expect("status: named law → status_lookup",
           classify("a është në fuqi Kodi Penal?").intent == "status_lookup")

    # precision: cultural-heritage inheritance must NOT map to the inheritance law
    expect("precision: trashëgimia kulturore not mapped",
           parse_citation("neni 5 i ligjit të trashëgimisë kulturore") is None)
    # named law + topic, no article → stays semantic (no opening-article overview)
    expect("precision: named law + topic → semantic",
           classify("dispozitat e ligjit të punës për pushimin vjetor").intent == "semantic_question")
    # numeric citations unchanged (by_name False)
    nc = parse_citation("neni 5 i Ligjit 02/L-10")
    expect("numeric citation: by_name False", nc is not None and not nc.by_name)

    # KUV code variants bridge to the inner form (index may store either)
    expect("variants bridge KUV → inner",
           "06/L-074" in law_number_variants("KUV-06/L-074-KOD"))


def test_incomplete_reference() -> None:
    print("\n[incomplete reference]")
    from app.ai.router import incomplete_reference
    from app.ai.pipeline import _refusal_message, _clarify_message_key

    # kind detection (strict = pre-routing)
    expect("kind: 'neni 37' → article_without_law",
           incomplete_reference("neni 37") == "article_without_law")
    expect("kind: 'ligji 04' → bare_law_prefix",
           incomplete_reference("ligji 04") == "bare_law_prefix")
    expect("kind: topical → None",
           incomplete_reference("Cilat janë dispozitat për shoqatat?") is None)
    expect("kind: complete citation → None",
           incomplete_reference("Neni 5 i Ligjit 02/L-10") is None)
    # strict vs loose: article + topical is None strict, but caught loose (net)
    expect("kind strict: article+topic → None",
           incomplete_reference("neni 37 për pushimet vjetore") is None)
    expect("kind loose: article+topic → article_without_law",
           incomplete_reference("neni 37 për pushimet vjetore", strict=False)
           == "article_without_law")

    # message selection
    expect("msg key: article → clarify_missing_law",
           _clarify_message_key("article_without_law") == "clarify_missing_law")
    expect("msg key: prefix → clarify",
           _clarify_message_key("bare_law_prefix") == "clarify")
    expect("refusal: article-only picks missing-law wording",
           "nen" in _refusal_message("neni 37", "sq").lower()
           and "numrin e ligjit" in _refusal_message("neni 37", "sq"))
    expect("refusal: topical keeps generic insufficient-context",
           "informacion të mjaftueshëm"
           in _refusal_message("Cilat janë dispozitat për shoqatat?", "sq"))


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
    test_text_norm()
    test_partial_turn_content()
    test_router()
    test_named_law_resolver()
    test_incomplete_reference()
    test_conversation()
    test_relevance_gate()
    test_long_query_gate()
    test_multilaw_gate()
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
