"""
Offline unit tests for the refusal clarifier (`app.ai.clarifier`) and its
wiring into `pipeline._refusal_message`.

No network: `app.ai.llm.complete` is monkeypatched. These pin down the
anti-fabrication guards — the deterministic discard rules that make the
clarifier safe to ship in a legal product — and the append-only contract
around the canonical refusal sentence.
"""

from __future__ import annotations

import pytest

import app.ai.llm as llm_mod
from app.ai.clarifier import _validate, generate_clarify_request


GOOD_CLARIFY_SQ = (
    "Për t'ju ndihmuar më saktë, ju lutem më tregoni:\n"
    "- Cilës fushë ligjore i përket pyetja (punësim, kontrata, familje...)?\n"
    "- A keni parasysh ndonjë ligj apo nen të caktuar?\n"
    "- Cilat janë rrethanat konkrete të situatës suaj?"
)


def _stub_complete(monkeypatch, text: str, capture: dict | None = None):
    def fake(messages, **kwargs):
        if capture is not None:
            capture["messages"] = messages
            capture["kwargs"] = kwargs
        return llm_mod.CompletionResult(
            text=text, model="deepseek-v4-flash",
            prompt_tokens=400, completion_tokens=60, cached_tokens=380,
            finish_reason="stop",
        )
    monkeypatch.setattr(llm_mod, "complete", fake)


# ---- output guards (the anti-fabrication contract) --------------------------

def test_clean_output_passes():
    assert _validate(GOOD_CLARIFY_SQ) == GOOD_CLARIFY_SQ


def test_law_number_discards_everything():
    # A single law number anywhere → the WHOLE text is discarded.
    assert _validate("Mendoni për Ligjin 03/L-212?\n- Cila fushë?") is None


def test_named_law_discards():
    # Curated named laws resolve via parse_citation → discard.
    assert _validate("A e keni parasysh kodin penal?") is None


def test_article_reference_discards():
    assert _validate("A mendoni për nenin 47 apo dicka tjeter?") is None


def test_english_article_reference_discards():
    assert _validate("Do you mean article 12 of some law?") is None


def test_unmapped_law_shape_discards():
    # A malformed number parse_citation can't resolve must ALSO not ship.
    assert _validate("Ndoshta ligji 999/L-9999 ju intereson?") is None


def test_unmik_year_shape_discards():
    assert _validate("Mos e keni fjalen per ligjin 2004/32?") is None


def test_overlong_output_discards():
    assert _validate("shume " * 200) is None


def test_empty_output_discards():
    assert _validate("") is None
    assert _validate("   \n ") is None


# ---- generate_clarify_request ------------------------------------------------

def test_happy_path(monkeypatch):
    cap: dict = {}
    _stub_complete(monkeypatch, GOOD_CLARIFY_SQ, cap)
    out = generate_clarify_request("cka duhet te di per rastin tim?", "sq")
    assert out == GOOD_CLARIFY_SQ
    # thinking must be disabled (V4 reasoning would eat the budget)
    assert cap["kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert cap["kwargs"]["fast"] is True


def test_llm_names_a_law_returns_none(monkeypatch):
    _stub_complete(monkeypatch, "Ndoshta Neni 5 i Ligjit 02/L-10 ju ndihmon?")
    assert generate_clarify_request("pyetje e paqarte", "sq") is None


def test_api_error_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_mod, "complete", boom)
    assert generate_clarify_request("pyetje e paqarte", "sq") is None


def test_empty_query_returns_none(monkeypatch):
    called = {}
    _stub_complete(monkeypatch, GOOD_CLARIFY_SQ, called)
    assert generate_clarify_request("  ", "sq") is None
    assert "kwargs" not in called  # no LLM call made


def test_long_query_parts_reach_the_prompt(monkeypatch):
    cap: dict = {}
    _stub_complete(monkeypatch, GOOD_CLARIFY_SQ, cap)
    parts = ["shoku im ka nje borxh te vjeter", "kontrata u nenshkrua ne 2020"]
    out = generate_clarify_request(
        "histori e gjate...", "sq", gate_tier="decomposed_all_below", parts=parts,
    )
    assert out == GOOD_CLARIFY_SQ
    user_msg = cap["messages"][1].content
    for p in parts:
        assert p in user_msg


# ---- pipeline wiring: append-only around the canonical refusal ---------------

def test_refusal_message_appends_after_canonical_sentence(monkeypatch):
    _stub_complete(monkeypatch, GOOD_CLARIFY_SQ)
    from app.ai.pipeline import _refusal_message, _MESSAGES

    trace: dict = {"relevance_floor_tier": "rerank"}
    out = _refusal_message("pyetje shume e paqarte pa teme", "sq", trace)
    canonical = _MESSAGES["sq"]["insufficient_context"]
    # The canonical refusal marker is intact at the START (append-only).
    assert out.startswith(canonical)
    assert GOOD_CLARIFY_SQ in out
    assert trace["refusal_clarify"]["used"] is True


def test_refusal_message_plain_when_clarifier_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(llm_mod, "complete", boom)
    from app.ai.pipeline import _refusal_message, _MESSAGES

    trace: dict = {}
    out = _refusal_message("pyetje shume e paqarte pa teme", "sq", trace)
    assert out == _MESSAGES["sq"]["insufficient_context"]
    assert trace["refusal_clarify"]["used"] is False


def test_refusal_message_incomplete_reference_still_steers(monkeypatch):
    # The existing clarify steering must take priority — no clarifier call.
    called = {}
    _stub_complete(monkeypatch, GOOD_CLARIFY_SQ, called)
    from app.ai.pipeline import _refusal_message, _MESSAGES

    out = _refusal_message("çfarë thotë neni 37?", "sq", {})
    assert out == _MESSAGES["sq"]["clarify_missing_law"]
    assert "kwargs" not in called


def test_refusal_message_english(monkeypatch):
    good_en = "To help you better, please tell me:\n- Which legal field is this about?"
    _stub_complete(monkeypatch, good_en)
    from app.ai.pipeline import _refusal_message, _MESSAGES

    out = _refusal_message("a very vague question about stuff", "en", {})
    assert out.startswith(_MESSAGES["en"]["insufficient_context"])
    assert good_en in out
