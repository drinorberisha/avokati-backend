"""
LLM-based intent router for AvokAI queries (DeepSeek-V4-Flash).

The regex router (`app.ai.router.classify`) is precise but literal: a query
phrased slightly differently than its patterns expect falls through to
semantic search (fine) or misses a greeting/out-of-scope/clarify verdict it
should have caught (not fine). This module routes with ONE cheap V4-Flash
call instead, capturing intent from meaning rather than surface form.

Design constraints (why this isn't just "ask the LLM"):

  1. The LLM NEVER supplies a canonical law number. It extracts the user's
     law reference as a verbatim substring (`law_ref`); the canonical number
     always comes from the deterministic parser/curated name map
     (`parse_citation`). An LLM-invented "04/L-xxx" would be a fabrication
     routed straight into a metadata filter — the exact failure the pipeline
     is built to prevent.
  2. Fail-safe: any API error, timeout, or malformed response falls back to
     the regex `classify()`, so the worst case is exactly today's behavior.
  3. Same output type (`RoutingDecision`) — everything downstream of routing
     (retrieval, gates G1-G3, generation) is unchanged.

Usage:
    result = classify_llm(query, context=context)
    result.decision      # RoutingDecision, same contract as router.classify()
    result.elapsed_ms    # wall-clock of the routing call
    result.fallback_reason  # non-None when the regex fallback was used

Cost: ~$0.00007/query at V4-Flash pricing; the system prompt is a stable
prefix so DeepSeek's prompt cache amortizes most of the input tokens.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

from app.ai.citation import Citation, parse_citation
from app.ai.conversation import ConversationContext
from app.ai.router import Intent, RoutingDecision, classify


# Valid intents the LLM may emit. Mirrors router.Intent.
_VALID_INTENTS = frozenset(
    {"greeting", "status_lookup", "citation_lookup", "semantic_question",
     "clarify", "out_of_scope"}
)

# Timeout for the routing call. Routing must never dominate latency — if
# Flash hasn't answered in this window, regex-fallback and move on.
ROUTER_LLM_TIMEOUT_S = float(os.environ.get("AVOKAI_ROUTER_LLM_TIMEOUT_S", "4.0"))

# Kill-switch for the fallback-mode LLM router (classify_with_fallback).
# Enabled by default: benchmarked at +9% intent accuracy on regex-fallthrough
# queries with ~1.3s added latency ONLY on those queries (see
# tests/eval/run_eval_router_llm.py). Set to "false" to restore pure-regex routing.
ROUTER_LLM_ENABLED = os.environ.get("AVOKAI_ROUTER_LLM_ENABLED", "true").lower() in ("true", "1", "yes")

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


# The system prompt is deliberately STATIC (no per-query interpolation) so the
# whole thing lands in DeepSeek's prompt-prefix cache. Few-shots cover the
# paraphrase/dialect cases the regex router misses; the intent definitions
# restate the routing contract from router.py.
SYSTEM_PROMPT = """\
You are the query router for AvokAI, a legal assistant that answers questions \
about the legislation in force in the Republic of Kosovo, using a corpus of \
Kosovo Official Gazette laws (in Albanian). Users write in Albanian (often \
Kosovo dialect, often without diacritics) or English.

Classify the user's query into EXACTLY ONE intent:

- "greeting": greetings/small talk with no legal question ("tung", "si jeni?", \
"hello", "faleminderit shume").
- "status_lookup": asks whether a SPECIFIC law (identified by number or name) \
is still in force / abolished / amended ("a eshte ende ne fuqi...", "a vlen \
ende...", "a eshte shfuqizuar...").
- "citation_lookup": asks for the content of a specific article of a specific \
law — both the article AND the law are identified ("neni 5 i ligjit 03/L-212", \
"çka thotë neni 12 i kodit penal"). Also: a specific law cited by its FULL \
NN/L-NNN number even without an article.
- "semantic_question": a substantive legal question about Kosovo law where the \
answer must be found by topic — no specific article+law pair given ("sa dite \
pushim vjetor kam?", "si themelohet nje shpk?", "çka parashikon ligji i punes \
per pushimin e lehonise?").
- "clarify": the query references a law or article too incompletely to look up: \
an article number with NO law and NO topic ("çfarë thotë neni 37?"), or a law \
number that is only the 1-2 digit legislature prefix ("ligji 04", "ligji 04/L") \
— the full form is NN/L-NNN.
- "out_of_scope": not about Kosovo legislation in force: foreign/comparative \
law (USA, France, Germany...), EU law and institutions, legal philosophy/theory \
(Hart, Kelsen...), world trivia, or non-legal topics (cooking, medicine, \
relationships, hacking).

Also extract, when present:
- "law_ref": the user's law reference COPIED VERBATIM from the query (a number \
like "03/L-212" or "2004/32", or a name like "ligji i punës", "kodi penal"). \
null if none. NEVER invent or complete a law number the user did not write.
- "article_number": the article number as digits ("5"), null if none.
- "uses_context": true ONLY if the query is a follow-up that refers back to a \
previously discussed law/article ("po neni 38?", "shpjego me shume kete nen", \
"a eshte ende ne fuqi ky ligj?") instead of naming its own.

Rules:
- A topical question that merely NAMES a law ("çka thotë ligji i punës për \
pagat?") with no article is "semantic_question", not "citation_lookup".
- A named law + article ("neni 5 i kodit penal") IS "citation_lookup".
- Questions about Kosovo law phrased informally, with typos, dialect (qysh, \
qka, shka, a bon...), or in English are still legal questions — never \
"out_of_scope" for style alone.
- When unsure between "semantic_question" and anything else, choose \
"semantic_question" (it is the safe default: full retrieval still runs).
- Output ONLY a JSON object, no prose, no markdown fence:
{"intent": "...", "law_ref": ... , "article_number": ..., "uses_context": ...}

Examples:
Q: "tung a je?" -> {"intent": "greeting", "law_ref": null, "article_number": null, "uses_context": false}
Q: "pershendetje, sa dite pushim vjetor me takojne?" -> {"intent": "semantic_question", "law_ref": null, "article_number": null, "uses_context": false}
Q: "qysh bohet me e regjistru nje biznes ne kosove" -> {"intent": "semantic_question", "law_ref": null, "article_number": null, "uses_context": false}
Q: "Çfarë thotë neni 47 i ligjit 04/L-077?" -> {"intent": "citation_lookup", "law_ref": "04/L-077", "article_number": "47", "uses_context": false}
Q: "neni 12 i kodit te procedures penale" -> {"intent": "citation_lookup", "law_ref": "kodit te procedures penale", "article_number": "12", "uses_context": false}
Q: "a eshte ende ne fuqi ligji 2004/32?" -> {"intent": "status_lookup", "law_ref": "2004/32", "article_number": null, "uses_context": false}
Q: "a vlen ende ligji i familjes apo ka ndryshu?" -> {"intent": "status_lookup", "law_ref": "ligji i familjes", "article_number": null, "uses_context": false}
Q: "çka thotë neni 37?" -> {"intent": "clarify", "law_ref": null, "article_number": "37", "uses_context": false}
Q: "me trego nenin 47 te ligjit 04" -> {"intent": "clarify", "law_ref": "ligjit 04", "article_number": "47", "uses_context": false}
Q: "po neni 38 çka thote?" (mid-conversation) -> {"intent": "citation_lookup", "law_ref": null, "article_number": "38", "uses_context": true}
Q: "shpjego me shume" (mid-conversation) -> {"intent": "citation_lookup", "law_ref": null, "article_number": null, "uses_context": true}
Q: "çfarë thotë kushtetuta e SHBA për armët?" -> {"intent": "out_of_scope", "law_ref": null, "article_number": null, "uses_context": false}
Q: "me jep nje recete per flija" -> {"intent": "out_of_scope", "law_ref": null, "article_number": null, "uses_context": false}
Q: "sipas Kelzenit, çka eshte norma themelore?" -> {"intent": "out_of_scope", "law_ref": null, "article_number": null, "uses_context": false}\
"""


@dataclass(frozen=True)
class LLMRouteResult:
    """Outcome of one classify_llm() call, with observability fields."""

    decision: RoutingDecision
    elapsed_ms: int
    model: str | None = None          # None when the regex fallback answered
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    raw_response: str | None = None   # what the LLM actually said (debugging)
    fallback_reason: str | None = None  # non-None → regex classify() was used


def _resolve_law_ref(law_ref: str | None, query: str) -> Citation | None:
    """Turn the LLM's verbatim `law_ref` into a verified Citation.

    Resolution is strictly deterministic: run `parse_citation` over the whole
    query first (it sees article + law together), then over the extracted
    substring alone (helps when the LLM isolated a reference the full-query
    scan ordered differently). No parse → None; the caller downgrades the
    route rather than trust an unverified reference.
    """
    cit = parse_citation(query)
    if cit is not None:
        return cit
    if law_ref:
        # Guard against hallucination: the ref must actually appear in the
        # query (whitespace/case-insensitive), otherwise ignore it.
        norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()  # noqa: E731
        if norm(law_ref) in norm(query):
            return parse_citation(str(law_ref))
    return None


def _decision_from_payload(
    payload: dict,
    query: str,
    context: ConversationContext | None,
) -> RoutingDecision:
    """Map the LLM's JSON verdict to a RoutingDecision with deterministic guards."""
    intent = str(payload.get("intent", "")).strip().lower()
    if intent not in _VALID_INTENTS:
        raise ValueError(f"invalid intent {intent!r}")

    law_ref = payload.get("law_ref") or None
    article = payload.get("article_number")
    article = str(article) if article not in (None, "") else None
    uses_context = bool(payload.get("uses_context"))

    if intent in ("greeting", "out_of_scope"):
        return RoutingDecision(intent, None, f"llm:{intent}")

    if intent == "clarify":
        # Pick the specific clarify wording the pipeline already localizes.
        reason = "article_without_law" if (article and not law_ref) else "bare_law_prefix"
        return RoutingDecision("clarify", None, f"llm:{reason}")

    if intent in ("status_lookup", "citation_lookup"):
        citation = _resolve_law_ref(law_ref, query)
        if citation is not None and article and citation.article_number is None:
            citation = Citation(
                law_number=citation.law_number,
                article_number=article,
                raw_law=citation.raw_law,
                by_name=citation.by_name,
            )
        # Follow-up: inherit the conversation focus when the query names no law.
        if citation is None and uses_context and context is not None and context.has_focus:
            citation = Citation(
                law_number=context.focus_law,  # type: ignore[arg-type]
                article_number=article or context.focus_article,
                raw_law=context.focus_law or "",
            )
        if citation is not None:
            return RoutingDecision(intent, citation, f"llm:{intent}")
        # The LLM asserted a lookup but no reference could be VERIFIED.
        # Downgrade instead of filtering on an unverified number: an article
        # with no resolvable law is the classic clarify case; a named-but-
        # unmapped law goes to semantic search (today's behavior for it).
        if article and not law_ref:
            return RoutingDecision("clarify", None, "llm:article_without_law_unresolved")
        return RoutingDecision(
            "semantic_question", None, f"llm:{intent}_unresolved_ref"
        )

    return RoutingDecision("semantic_question", None, "llm:semantic_question")


def _parse_response(text: str) -> dict:
    """Extract the JSON object from the model's reply (tolerates stray prose/fences)."""
    m = _JSON_BLOCK_RE.search(text or "")
    if not m:
        raise ValueError(f"no JSON object in response: {text[:200]!r}")
    payload = json.loads(m.group(0))
    if not isinstance(payload, dict):
        raise ValueError("response JSON is not an object")
    return payload


def classify_llm(
    query: str,
    context: ConversationContext | None = None,
    *,
    timeout_s: float = ROUTER_LLM_TIMEOUT_S,
) -> LLMRouteResult:
    """Route `query` with one V4-Flash call; regex `classify()` on any failure.

    Mirrors `router.classify(query, context)` but decides from meaning, not
    patterns. Empty queries and API failures never raise — the regex router
    answers and `fallback_reason` records why.
    """
    t0 = time.time()

    if not query or not query.strip():
        return LLMRouteResult(
            decision=classify(query, context=context),
            elapsed_ms=int((time.time() - t0) * 1000),
            fallback_reason="empty_query",
        )

    user_content = query.strip()
    if context is not None and context.has_focus:
        # Give the model the conversation focus so `uses_context` follow-ups
        # are classifiable. Appended to the USER message — the system prompt
        # stays byte-identical for prompt caching.
        focus = f"law {context.focus_law}"
        if context.focus_article:
            focus += f", article {context.focus_article}"
        user_content = f"[conversation focus: {focus}]\n{user_content}"

    try:
        from app.ai.llm import ChatMessage, complete

        result = complete(
            [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user_content)],
            fast=True,
            temperature=0.0,
            max_tokens=120,
            timeout=timeout_s,
            # V4 models reason by default; on this classification task the
            # reasoning burned the whole token budget BEFORE the JSON (empty
            # content, finish=length) and ~doubled latency. Routing needs the
            # verdict, not the deliberation.
            extra_body={"thinking": {"type": "disabled"}},
        )
        payload = _parse_response(result.text)
        decision = _decision_from_payload(payload, query, context)
        return LLMRouteResult(
            decision=decision,
            elapsed_ms=int((time.time() - t0) * 1000),
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cached_tokens=result.cached_tokens,
            cost_usd=result.usd_cost_estimate(),
            raw_response=result.text,
        )
    except Exception as e:  # noqa: BLE001 — routing must never break the pipeline
        return LLMRouteResult(
            decision=classify(query, context=context),
            elapsed_ms=int((time.time() - t0) * 1000),
            fallback_reason=f"{type(e).__name__}: {e}",
        )


def classify_with_fallback(
    query: str,
    context: ConversationContext | None = None,
) -> tuple[RoutingDecision, dict | None]:
    """Fallback-mode routing: regex first, LLM only where regex has no opinion.

    Benchmarked (tests/eval/run_eval_router_llm.py, 68 queries): on queries the
    regex router matches POSITIVELY the two routers agree 100%, so the LLM call
    is pure latency there — it only runs on `default_fallthrough`, where it
    fixed 6/7 regex misses (paraphrased greetings, dialect status queries,
    foreign-law refusals, incomplete references).

    Returns `(decision, llm_trace)`. `llm_trace` is None when the LLM wasn't
    consulted; otherwise a small dict for route_trace observability. Never
    raises; worst case is exactly the regex decision.
    """
    decision = classify(query, context=context)
    if not ROUTER_LLM_ENABLED or decision.reason != "default_fallthrough":
        return decision, None

    result = classify_llm(query, context=context)
    llm_trace = {
        "elapsed_ms": result.elapsed_ms,
        "model": result.model,
        "cost_usd": round(result.cost_usd, 8),
        "fallback_reason": result.fallback_reason,
        "intent": result.decision.intent,
        "reason": result.decision.reason,
    }
    if result.fallback_reason is not None:
        # LLM routing failed (API error / bad JSON / timeout) — the regex
        # decision stands; the trace records why.
        return decision, llm_trace
    return result.decision, llm_trace


__all__ = [
    "LLMRouteResult",
    "ROUTER_LLM_ENABLED",
    "ROUTER_LLM_TIMEOUT_S",
    "SYSTEM_PROMPT",
    "classify_llm",
    "classify_with_fallback",
]
