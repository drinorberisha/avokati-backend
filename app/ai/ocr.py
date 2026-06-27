"""
Gemini multimodal OCR for PDFs whose TEXT LAYER is corrupt.

Some gazette PDFs (mostly InDesign-exported 07/L-/08/L- laws + the Customs Code)
reference a non-embedded font with a broken/custom encoding and no ToUnicode
CMap. They render fine to the eye, but PyMuPDF's extractable text maps Albanian
ë/ç to `€`/digits ("Kosovës" -> "Kosov6s"). See scripts/audit_garbled_chunks.py.

The fix is to render each page to an image and transcribe the *visible* glyphs
with Gemini — which ignores the broken text layer entirely. Verified: a page
PyMuPDF extracts at 19% garble comes back at 0%.

No SDK dependency — just httpx (already used by gemini_extract.py).
"""

from __future__ import annotations

import base64
import os
import re
import time

import httpx

# Table-of-contents / schedule "dot leaders" ("Neni 5 .......... 12") get
# transcribed verbatim by OCR as huge runs of dots — thousands of chars of junk
# that bloat the text 3-4x and create meaningless chunks. Collapse any run of 4+
# dots (ellipsis "..." is kept) and long dash/underscore fill to a single space.
_DOT_LEADER = re.compile(r"[ \t]*\.(?:[ \t]*\.){3,}[ \t]*")
_LONG_FILL = re.compile(r"([-_=•·~])\1{4,}")
_MANY_SPACES = re.compile(r"[ \t]{3,}")


def _normalize_ocr(text: str) -> str:
    text = _DOT_LEADER.sub(" ", text)
    text = _LONG_FILL.sub(" ", text)
    return _MANY_SPACES.sub("  ", text)

GEMINI_MODEL = os.environ.get("GEMINI_OCR_MODEL", "gemini-2.5-flash")
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_PROMPT = (
    "Transkripto TËRË tekstin nga kjo faqe e dokumentit ligjor EKZAKTËSISHT siç është, "
    "në gjuhën origjinale (shqip). Jep VETËM tekstin e transkriptuar — pa koment, pa "
    "përkthim, pa përmbledhje.\n"
    "STRUKTURA E DETYRUESHME: çdo titull neni vendose në DY rreshta — së pari vetëm "
    "'Neni N' i vetëm në një rresht (vetëm fjala Neni dhe numri), pastaj titulli "
    "përshkrues i nenit në rreshtin pasues. Mos e vendos titullin në të njëjtin rresht "
    "me 'Neni N'. Ruaj 'Kreu' dhe titujt e tjerë strukturorë në rreshtat e tyre.\n"
    "MOS i riprodho vargjet e gjata me pika ose vija (p.sh. '..........' ose '--------') "
    "që shërbejnë vetëm si pikëzim në tabelën e përmbajtjes — zëvendëso secilin varg të "
    "tillë me një hapësirë të vetme. Mos shkruaj kurrë më shumë se tri pika radhazi."
)


def _ocr_png(png: bytes, api_key: str, *, max_tokens: int = 65536, retries: int = 4) -> tuple[str, str | None]:
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}},
            {"text": _PROMPT},
        ]}],
        # OCR is a perception task, not reasoning. Gemini 2.5 Flash enables
        # "thinking" by default and those tokens count against maxOutputTokens —
        # on dense pages they exhausted the budget and the call returned
        # finishReason=MAX_TOKENS with truncated/empty text (08_L-009, 08_L-106
        # both died on page 8). Disabling thinking fixes that and cuts cost+latency.
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.post(_URL.format(model=GEMINI_MODEL), params={"key": api_key}, json=body, timeout=180)
            r.raise_for_status()
            cand = (r.json().get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            return text, cand.get("finishReason")
        except httpx.HTTPStatusError as e:
            # 4xx (except 429 rate-limit) won't fix on retry.
            if e.response.status_code < 500 and e.response.status_code != 429:
                raise
            last = e
        except httpx.TransportError as e:  # ConnectError (DNS blips), timeouts, resets
            last = e
        time.sleep(2 ** attempt)  # 1, 2, 4, 8s backoff
    raise last  # type: ignore[misc]


def ocr_pdf_text(
    pdf_path: str,
    *,
    dpi: int = 200,
    page_selective: bool = False,
    garble_threshold: float = 0.03,
) -> str:
    """Render each page and transcribe it with Gemini; return concatenated text.

    With ``page_selective=True`` only the pages whose PyMuPDF text is actually
    garbled (``garble_ratio`` above ``garble_threshold``) are sent to OCR; clean
    pages keep their exact PyMuPDF text. Because that text is extracted the same
    way as ``build_v2_index._extract_text`` (``"\\n".join(page.get_text("text"))``),
    the clean pages come out byte-identical to what's already indexed — so only
    the broken chunks change and the OCR cost is spent only where it's needed
    (e.g. 1 page of a 478-page law, not all 478).

    Raises if GEMINI_API_KEY is missing or a page truncates (MAX_TOKENS) so the
    caller never silently re-ingests a partial law.
    """
    import fitz

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set — required for OCR re-extraction.")

    garble_ratio = None
    if page_selective:
        from scripts.audit_garbled_chunks import garble_ratio  # one source of truth

    doc = fitz.open(pdf_path)
    try:
        pages: list[str] = []
        n_ocr = 0
        for i, page in enumerate(doc):
            if page_selective:
                pm_text = page.get_text("text")
                if garble_ratio(pm_text) <= garble_threshold:
                    pages.append(pm_text)  # clean page → keep verbatim, no OCR cost/churn
                    continue
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            text, finish = _ocr_png(png, api_key)
            if finish not in (None, "STOP"):
                raise RuntimeError(f"OCR page {i} of {pdf_path} did not finish cleanly: {finish}")
            pages.append(text)
            n_ocr += 1
        if page_selective:
            print(f"    [ocr] {pdf_path}: OCR'd {n_ocr}/{len(doc)} garbled pages "
                  f"(kept {len(doc) - n_ocr} clean)")
        return _normalize_ocr("\n".join(pages))
    finally:
        doc.close()


__all__ = ["ocr_pdf_text"]
