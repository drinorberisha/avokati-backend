"""
Offline tests for the two fixes behind the "neni 47 i ligjit 4 l77" failure:

1. Zero-padding: user-typed law numbers without the gazette's zero padding
   ("4 l77") must canonicalize/probe to the indexed form ("04/L-077").
2. Clarify-completion merge: after a turn that referenced an article with an
   incomplete law, a law-only follow-up ("ligji 4 l 077") inherits that
   article instead of doing a bare law lookup.
"""

from __future__ import annotations

from app.ai.citation import canonicalize_law_number, law_number_variants, parse_citation
from app.ai.conversation import derive_context
from app.ai.router import classify


# ---- zero-padding ------------------------------------------------------------

def test_series_is_zero_padded():
    assert canonicalize_law_number("4 l77") == "04/L-77"
    assert canonicalize_law_number("4 l 077") == "04/L-077"
    assert canonicalize_law_number("8L035") == "08/L-035"


def test_already_padded_unchanged():
    assert canonicalize_law_number("04/L-077") == "04/L-077"
    assert canonicalize_law_number("02/L-10") == "02/L-10"


def test_kuv_and_unmik_shapes_unchanged():
    assert canonicalize_law_number("KUV-08/L-032-KOD") == "KUV-08/L-032-KOD"
    assert canonicalize_law_number("2004/32") == "2004/32"


def test_variants_probe_padded_number_forms():
    v = law_number_variants("4 l77")
    assert v[0] == "04/L-77"          # canonical (as typed) first
    assert "04/L-077" in v            # 3-digit padded form probed
    v2 = law_number_variants("04/L-077")
    assert v2[0] == "04/L-077"
    assert "04/L-77" in v2            # stripped form probed too


def test_variants_unmik_unaffected():
    v = law_number_variants("2004/32")
    assert v[0] == "2004/32"
    assert all("/L-" not in x for x in v)


def test_parse_citation_user_flow_turn1():
    cit = parse_citation("neni 47 i ligjit 4 l77")
    assert cit is not None
    assert cit.law_number == "04/L-77"
    assert cit.article_number == "47"


# ---- clarify-completion merge --------------------------------------------------

_HIST_CLARIFY = [
    {"role": "user", "content": "neni 47 i ligjit 4 l77"},
    {"role": "assistant", "content": "Numri i ligjit duket i paplotë. Ju lutem më jepni numrin e plotë (p.sh. *Neni 47 i Ligjit 04/L-077*)."},
]


def test_law_only_completion_inherits_article():
    ctx = derive_context(_HIST_CLARIFY)
    assert ctx.last_user_article == "47"
    d = classify("ligji 4 l 077", context=ctx)
    assert d.intent == "citation_lookup"
    assert d.reason == "law_completion_inherited_article"
    assert d.citation.law_number == "04/L-077"
    assert d.citation.article_number == "47"


def test_status_query_never_merges():
    ctx = derive_context(_HIST_CLARIFY)
    d = classify("a eshte ne fuqi ligji 04/L-077?", context=ctx)
    assert d.intent == "status_lookup"
    assert d.citation.article_number is None


def test_topical_question_never_merges():
    ctx = derive_context(_HIST_CLARIFY)
    d = classify("çka thotë ligji 04/L-077 për pagat?", context=ctx)
    assert d.intent == "citation_lookup"
    assert d.reason == "citation_parsed"
    assert d.citation.article_number is None


def test_own_article_wins_over_context():
    ctx = derive_context(_HIST_CLARIFY)
    d = classify("neni 12 i ligjit 04/L-077", context=ctx)
    assert d.citation.article_number == "12"


def test_no_merge_when_previous_user_turn_had_no_article():
    hist = [
        {"role": "user", "content": "neni 47 i ligjit 4 l77"},
        {"role": "assistant", "content": "Numri i ligjit duket i paplotë..."},
        {"role": "user", "content": "ligji 4 l 077"},
        {"role": "assistant", "content": "Neni 47 i Ligjit 04/L-077 thotë... [Neni 47, Ligji 04/L-077]"},
    ]
    ctx = derive_context(hist)
    # Most recent USER turn ("ligji 4 l 077") has no article ref → one-turn
    # window empty → a fresh law-only question does NOT inherit article 47.
    assert ctx.last_user_article is None
    d = classify("ligji 08/L-035", context=ctx)
    assert d.reason == "citation_parsed"
    assert d.citation.article_number is None


def test_stateless_behavior_unchanged():
    d = classify("ligji 4 l 077")
    assert d.intent == "citation_lookup"
    assert d.reason == "citation_parsed"
    assert d.citation.article_number is None
