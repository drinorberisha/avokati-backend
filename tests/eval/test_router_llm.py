"""
Offline unit tests for the LLM router (`app.ai.router_llm`).

No network: `app.ai.llm.complete` is monkeypatched to return canned JSON, so
these test the DETERMINISTIC half of classify_llm — payload→RoutingDecision
mapping, citation verification, hallucination guards, and the regex fallback.
Live latency/accuracy is measured separately by run_eval_router_llm.py.
"""

from __future__ import annotations

import json

import pytest

import app.ai.llm as llm_mod
from app.ai.conversation import ConversationContext
from app.ai.router_llm import classify_llm


def _fake_complete(payload_or_text, **capture):
    """Build a stand-in for llm.complete returning `payload_or_text` as the reply."""
    text = (
        payload_or_text
        if isinstance(payload_or_text, str)
        else json.dumps(payload_or_text)
    )

    def fake(messages, **kwargs):
        capture["messages"] = messages
        capture["kwargs"] = kwargs
        return llm_mod.CompletionResult(
            text=text,
            model="deepseek-v4-flash",
            prompt_tokens=900,
            completion_tokens=30,
            cached_tokens=850,
            finish_reason="stop",
        )

    return fake


def _route(monkeypatch, query, payload_or_text, context=None):
    monkeypatch.setattr(llm_mod, "complete", _fake_complete(payload_or_text))
    return classify_llm(query, context=context)


# ---- direct intents -------------------------------------------------------

def test_greeting(monkeypatch):
    r = _route(monkeypatch, "tung a je?", {"intent": "greeting"})
    assert r.decision.intent == "greeting"
    assert r.decision.citation is None
    assert r.fallback_reason is None
    assert r.model == "deepseek-v4-flash"


def test_out_of_scope(monkeypatch):
    r = _route(monkeypatch, "kushtetuta e SHBA per armet", {"intent": "out_of_scope"})
    assert r.decision.intent == "out_of_scope"


def test_semantic(monkeypatch):
    r = _route(
        monkeypatch,
        "qysh bohet me e regjistru nje biznes",
        {"intent": "semantic_question", "law_ref": None, "article_number": None},
    )
    assert r.decision.intent == "semantic_question"


# ---- clarify wording selection --------------------------------------------

def test_clarify_article_without_law(monkeypatch):
    r = _route(
        monkeypatch,
        "çka thote neni 37?",
        {"intent": "clarify", "law_ref": None, "article_number": "37"},
    )
    assert r.decision.intent == "clarify"
    assert "article_without_law" in r.decision.reason


def test_clarify_bare_law_prefix(monkeypatch):
    r = _route(
        monkeypatch,
        "neni 47 i ligjit 04",
        {"intent": "clarify", "law_ref": "ligjit 04", "article_number": "47"},
    )
    assert r.decision.intent == "clarify"
    assert "bare_law_prefix" in r.decision.reason


# ---- citation resolution: deterministic, never LLM-invented ----------------

def test_citation_number_from_query(monkeypatch):
    r = _route(
        monkeypatch,
        "Çfarë thotë neni 47 i ligjit 04/L-077?",
        {"intent": "citation_lookup", "law_ref": "04/L-077", "article_number": "47"},
    )
    assert r.decision.intent == "citation_lookup"
    assert r.decision.citation.law_number == "04/L-077"
    assert r.decision.citation.article_number == "47"


def test_citation_named_law_resolved_by_curated_map(monkeypatch):
    r = _route(
        monkeypatch,
        "neni 12 i kodit penal",
        {"intent": "citation_lookup", "law_ref": "kodit penal", "article_number": "12"},
    )
    assert r.decision.intent == "citation_lookup"
    # Canonical number must come from the curated _NAMED_LAW_PATTERNS map.
    assert r.decision.citation.law_number == "KUV-06/L-074-KOD"
    assert r.decision.citation.article_number == "12"


def test_hallucinated_law_ref_is_ignored(monkeypatch):
    # LLM claims a law number that is NOT in the query → must not become a filter.
    r = _route(
        monkeypatch,
        "sa dite pushim vjetor kam?",
        {"intent": "citation_lookup", "law_ref": "03/L-212", "article_number": None},
    )
    assert r.decision.intent == "semantic_question"
    assert r.decision.citation is None
    assert "unresolved" in r.decision.reason


def test_unmapped_named_law_downgrades_to_semantic(monkeypatch):
    r = _route(
        monkeypatch,
        "çka thote ligji per mbrojtjen e konsumatorit?",
        {"intent": "citation_lookup", "law_ref": "ligji per mbrojtjen e konsumatorit",
         "article_number": None},
    )
    assert r.decision.intent == "semantic_question"


def test_asserted_article_lookup_without_law_becomes_clarify(monkeypatch):
    r = _route(
        monkeypatch,
        "me trego nenin 5",
        {"intent": "citation_lookup", "law_ref": None, "article_number": "5"},
    )
    assert r.decision.intent == "clarify"


def test_status_lookup_with_number(monkeypatch):
    r = _route(
        monkeypatch,
        "a eshte ende ne fuqi ligji 2004/32?",
        {"intent": "status_lookup", "law_ref": "2004/32", "article_number": None},
    )
    assert r.decision.intent == "status_lookup"
    assert r.decision.citation.law_number == "2004/32"


# ---- follow-up context inheritance -----------------------------------------

def test_followup_inherits_focus(monkeypatch):
    ctx = ConversationContext(focus_law="03/L-212", focus_article="37")
    r = _route(
        monkeypatch,
        "po neni 38 çka thote?",
        {"intent": "citation_lookup", "law_ref": None, "article_number": "38",
         "uses_context": True},
        context=ctx,
    )
    assert r.decision.intent == "citation_lookup"
    assert r.decision.citation.law_number == "03/L-212"
    assert r.decision.citation.article_number == "38"


def test_followup_without_focus_downgrades(monkeypatch):
    r = _route(
        monkeypatch,
        "shpjego me shume",
        {"intent": "citation_lookup", "law_ref": None, "article_number": None,
         "uses_context": True},
        context=None,
    )
    assert r.decision.intent == "semantic_question"


# ---- fail-safe fallback ----------------------------------------------------

def test_invalid_intent_falls_back_to_regex(monkeypatch):
    r = _route(monkeypatch, "përshëndetje", {"intent": "banana"})
    assert r.fallback_reason is not None
    assert r.decision.intent == "greeting"  # regex classify() answered


def test_malformed_json_falls_back(monkeypatch):
    r = _route(monkeypatch, "Çfarë thotë neni 5 i ligjit 02/L-10?", "sorry, no JSON here")
    assert r.fallback_reason is not None
    assert r.decision.intent == "citation_lookup"
    assert r.decision.citation.law_number == "02/L-10"


def test_api_error_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(llm_mod, "complete", boom)
    r = classify_llm("a eshte ne fuqi ligji 04/L-077?")
    assert r.fallback_reason is not None
    assert r.decision.intent == "status_lookup"


def test_empty_query_short_circuits(monkeypatch):
    called = {}
    monkeypatch.setattr(llm_mod, "complete", _fake_complete({"intent": "greeting"}, **called))
    r = classify_llm("   ")
    assert r.fallback_reason == "empty_query"
    assert "kwargs" not in called  # no LLM call was made


def test_json_extracted_from_markdown_fence(monkeypatch):
    r = _route(
        monkeypatch,
        "tung",
        '```json\n{"intent": "greeting", "law_ref": null, "article_number": null, "uses_context": false}\n```',
    )
    assert r.decision.intent == "greeting"
    assert r.fallback_reason is None
