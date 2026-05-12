"""
LLM client factory for AvokAI.

DeepSeek-V4-Pro is the primary generation model; V4-Flash is for routing
and other low-stakes calls. Both speak the OpenAI Chat Completions API at
`https://api.deepseek.com/v1`, so we use the OpenAI SDK with a base-URL
override — no extra dependencies, drop-in replacement for ChatGPT-class
clients.

Why DeepSeek over GPT-4o:
  - Per-token cost ~10–20× lower (V4-Pro: $0.435/M input cache-miss,
    $0.87/M output; V4-Flash: $0.14/M input, $0.28/M output)
  - 1M-token context window (vs. 128K for GPT-4o), 384K max output
  - Strong multilingual including Albanian
  - Aggressive prompt-prefix caching: stable system+context prefix can drop
    input cost from $0.435/M to $0.003625/M on cache hit (~120× cheaper)

Embeddings stay on OpenAI — DeepSeek does not expose an embeddings
endpoint (confirmed against their API reference: only `/chat/completions`,
`/completions`, `/list-models`, `/balance`).

DO NOT USE: `deepseek-chat` and `deepseek-reasoner` — DeepSeek deprecates
those names on 2026/07/24. Always target `deepseek-v4-*`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Locked model names. Override in env for canary testing only.
PRIMARY_MODEL = os.environ.get("DEEPSEEK_MODEL_PRIMARY", "deepseek-v4-pro")
FAST_MODEL = os.environ.get("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")


@dataclass(frozen=True)
class ChatMessage:
    role: str            # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class CompletionResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    finish_reason: str | None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def usd_cost_estimate(self) -> float:
        """Rough cost estimate (USD) at current promo pricing.

        Treat the response as a guideline only — actual billing may differ
        once the 75% promo discount expires. Re-tune from DeepSeek's
        pricing page periodically.
        """
        # V4-Pro promo rates (per 1M tokens, USD)
        if self.model.startswith("deepseek-v4-pro"):
            cache_miss_in = 0.435
            cache_hit_in = 0.003625
            output = 0.87
        elif self.model.startswith("deepseek-v4-flash"):
            cache_miss_in = 0.14
            cache_hit_in = 0.0035  # approximate; flash cache pricing not separately disclosed
            output = 0.28
        else:
            return 0.0
        cache_hit = self.cached_tokens
        cache_miss = max(0, self.prompt_tokens - cache_hit)
        cost = (
            cache_miss * cache_miss_in / 1_000_000
            + cache_hit * cache_hit_in / 1_000_000
            + self.completion_tokens * output / 1_000_000
        )
        return cost


def get_client():
    """Return a sync OpenAI SDK client configured for the DeepSeek API.

    Lazy-imported so this module is still importable without `openai`
    installed (e.g. during chunker-only unit tests).
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. Add it to backend/.env to enable "
            "AvokAI generation. Embeddings still use OPENAI_API_KEY."
        )
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def get_async_client():
    """Async OpenAI SDK client for streaming (used by SSE endpoint).

    Separate from the sync client because the SDK keeps a per-client
    connection pool; mixing them in the same module is fine but each is
    its own resource.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. Add it to backend/.env to enable "
            "AvokAI generation. Embeddings still use OPENAI_API_KEY."
        )
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def complete(
    messages: list[ChatMessage] | list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    fast: bool = False,
) -> CompletionResult:
    """One-shot chat completion against DeepSeek.

    Args:
        messages: list of ChatMessage or OpenAI-style dicts.
        model: explicit model name. Overrides `fast`. If None, picks
            FAST_MODEL when `fast=True` else PRIMARY_MODEL.
        temperature: 0.1 by default — legal Q&A wants determinism.
        max_tokens: cap on output length; None lets the model decide
            (capped server-side by the model's max_output).
        fast: route to V4-Flash for cheap classification / routing.

    Returns a CompletionResult including the raw text and token usage so
    the caller can log cost.
    """
    client = get_client()
    model_name = model or (FAST_MODEL if fast else PRIMARY_MODEL)

    if messages and isinstance(messages[0], ChatMessage):
        payload = [{"role": m.role, "content": m.content} for m in messages]
    else:
        payload = list(messages)  # type: ignore[arg-type]

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": payload,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    usage = getattr(resp, "usage", None)
    # DeepSeek exposes prompt-cache hit token counts under
    # `usage.prompt_cache_hit_tokens` (their docs). Fall back to 0 if missing.
    cached = 0
    if usage is not None:
        cached = (
            getattr(usage, "prompt_cache_hit_tokens", None)
            or getattr(usage, "cached_tokens", None)
            or 0
        )

    return CompletionResult(
        text=choice.message.content or "",
        model=model_name,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        cached_tokens=int(cached),
        finish_reason=choice.finish_reason,
    )


async def acomplete_stream(
    messages: list[ChatMessage] | list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    fast: bool = False,
):
    """Async streaming completion against DeepSeek.

    Async generator yielding `("delta", str)` for each token chunk, ending
    with `("done", CompletionResult)` where the result has the full
    accumulated text and final usage counts. Usage tokens are only present
    in the final chunk when `stream_options={"include_usage": True}` is
    set, which DeepSeek (OpenAI-compatible) supports.

    Designed for the SSE `/ask-v2/stream` endpoint — yields fast (sub-100ms
    to first chunk on warm path), so users see Albanian text appear within
    a few seconds instead of waiting for the full ~2-3 minute generation.
    """
    client = get_async_client()
    model_name = model or (FAST_MODEL if fast else PRIMARY_MODEL)

    if messages and isinstance(messages[0], ChatMessage):
        payload = [{"role": m.role, "content": m.content} for m in messages]
    else:
        payload = list(messages)  # type: ignore[arg-type]

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": payload,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    full_text_parts: list[str] = []
    finish_reason: str | None = None
    prompt_tokens = completion_tokens = cached_tokens = 0

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if choices:
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is not None:
                text = getattr(delta, "content", None) or ""
                if text:
                    full_text_parts.append(text)
                    yield ("delta", text)
            fr = getattr(choice, "finish_reason", None)
            if fr:
                finish_reason = fr
        # The final chunk in stream mode (with include_usage=True) carries
        # the usage block under `usage` and no `choices` content.
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            cached_tokens = (
                getattr(usage, "prompt_cache_hit_tokens", None)
                or getattr(usage, "cached_tokens", None)
                or 0
            )

    yield (
        "done",
        CompletionResult(
            text="".join(full_text_parts),
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=int(cached_tokens),
            finish_reason=finish_reason,
        ),
    )


__all__ = [
    "PRIMARY_MODEL",
    "FAST_MODEL",
    "ChatMessage",
    "CompletionResult",
    "get_client",
    "get_async_client",
    "complete",
    "acomplete_stream",
]
