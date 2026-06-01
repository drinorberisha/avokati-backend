"""Template extraction via Google Gemini 2.5 Flash (multimodal).

Gemini ingests the raw document — PDF (digital OR scanned), or an image — and
returns a parameterized template JSON in one call. Unlike the PyMuPDF+DeepSeek
path, this reads scanned pages directly (built-in OCR), which is what most
notary/sale contracts need.

Uses the Generative Language REST API with an API key (GEMINI_API_KEY). No SDK
dependency — just httpx, which is already installed.
"""

import base64
import json
import os
from typing import Any

import httpx

from app.ai.prompts.template_extract import TEMPLATE_SYSTEM_PROMPT

GEMINI_MODEL = os.environ.get("GEMINI_TEMPLATE_MODEL", "gemini-2.5-flash")
_GENAI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Force Gemini to emit exactly our template shape.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["Contracts", "Legal", "HR", "Finance", "Court Filing"],
        },
        "language": {"type": "string"},
        "content_html": {"type": "string"},
        "variables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["text", "number", "date", "select", "boolean"],
                    },
                    "required": {"type": "boolean"},
                    "description": {"type": "string"},
                },
                "required": ["name", "type"],
            },
        },
    },
    "required": ["title", "content_html", "variables"],
}

# Mime types Gemini can read as inline_data for our purposes.
SUPPORTED_INLINE_MIME = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/heic",
    "image/heif",
}


def gemini_enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


async def _call_gemini(parts: list[dict]) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }
    url = _GENAI_URL.format(model=GEMINI_MODEL)
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, params={"key": api_key}, json=body)
    resp.raise_for_status()
    payload = resp.json()
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {payload}")
    text = "".join(
        part.get("text", "")
        for part in candidates[0].get("content", {}).get("parts", [])
    )
    if not text.strip():
        raise RuntimeError("Gemini returned empty content.")
    return json.loads(text)


async def extract_template_from_file(data: bytes, mime_type: str) -> dict:
    """Extract a template from raw document bytes (PDF/image)."""
    parts = [
        {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}},
        {"text": TEMPLATE_SYSTEM_PROMPT},
    ]
    return await _call_gemini(parts)


async def extract_template_from_text(text: str) -> dict:
    """Extract a template from already-extracted text (e.g. DOCX)."""
    parts = [{"text": TEMPLATE_SYSTEM_PROMPT + "\n\nDocument:\n\n" + text}]
    return await _call_gemini(parts)
