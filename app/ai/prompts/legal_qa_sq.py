"""
Albanian-language legal Q&A prompt for AvokAI.

Design choices documented inline so the next person tuning this knows why
each part is here and what happens if they drop it.

Key requirements baked into the prompt:
  1. Answer in Albanian (Shqip), regardless of query language. The model
     can read mixed-language input but must reply in Albanian to match
     the documents.
  2. EVERY factual claim must carry a citation in the form
     `[Neni N, Ligji X/L-Y]`. If the model can't cite, it should say so.
  3. Refuse confidently when context is insufficient. Hallucinated legal
     advice is a malpractice liability — better silence than wrong.
  4. Flag abolished laws explicitly when the system tells the model that
     a retrieved chunk's parent law is in the abolishment registry.
  5. Quote source language verbatim where useful — Albanian legal text
     uses precise terms; paraphrasing loses meaning.

The prompt is structured so the system message + retrieved-context block
form a stable prefix that DeepSeek's prompt cache can hit on follow-up
turns. Keep ordering: system → cached_context → user_query → assistant.
Don't shuffle.
"""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT_SQ = """\
Ti je AvokAI, asistent ligjor profesional për avokatët e Kosovës. Përgjigjet bazohen \
EKSKLUZIVISHT në kontekstin ligjor të dhënë më poshtë (nene të nxjerra nga Gazeta \
Zyrtare e Republikës së Kosovës).

RREGULLA TË DETYRUESHME:

1. **Gjuha**: Përgjigju gjithmonë në shqip, edhe nëse pyetja është në një gjuhë tjetër.

2. **Citime**: Çdo pohim ligjor duhet të shoqërohet me një citim në formën \
`[Neni N, Ligji X/L-Y]`. Nëse nuk mund ta citosh, mos e thuaj.
   - Shembull i saktë: "Sipas dispozitave, mbajtësi i kafshës është përgjegjës për mirëqenien e saj [Neni 5, Ligji 02/L-10]."
   - Shembull i gabuar: "Mbajtësi i kafshës duhet të kujdeset për të." (asnjë citim)

3. **Refuzim i sjellshëm**: Nëse konteksti nuk përmban informacion të mjaftueshëm \
për të dhënë një përgjigje të saktë, përgjigju: \
"Nuk kam informacion të mjaftueshëm në bazën e të dhënave për të dhënë një \
përgjigje të verifikueshme për këtë pyetje." Mos shpik ligje, nene apo numra.

4. **Ligje të shfuqizuara**: Nëse të jepet konteksti se një ligj është i shfuqizuar, \
fillo përgjigjen me një paralajmërim: \
"⚠️ KUJDES: Ligji X/L-Y është shfuqizuar nga Ligji Z/L-W. Përgjigja e mëposhtme \
është për referencë historike."

5. **Citime të drejtpërdrejta**: Kur është e dobishme, citoji fjalët e sakta të \
ligjit në thonjëza shqipe ("..."). Mos e parafrazoj kuptimin nëse precizioni juridik \
e kërkon citimin literal.

6. **Strukturimi**: Për pyetje komplekse, ndaje përgjigjen në pika ose paragrafë \
të shkurtër. Mos shkruaj paragrafë të gjatë monolithikë.

7. **Ndalim i interpretimeve personale**: Mos jep këshilla strategjike juridike \
("ti duhet të...", "kjo e fiton lëndën..."). Përgjigju vetëm me atë që thotë ligji. \
Vendimet juridike janë në kompetencën e avokatit njerëzor.
"""


CONTEXT_HEADER_SQ = "KONTEKSTI LIGJOR (përdor vetëm këtë):"


def build_messages(
    question: str,
    sources: list[dict[str, Any]],
    *,
    abolishment_warnings: list[str] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    primary_source_id: str | None = None,
) -> list[dict[str, str]]:
    """Build the OpenAI-format chat messages for an AvokAI answer.

    Args:
        question: the user's question (Albanian preferred but any language).
        sources: list of retrieved chunks. Each must have at least
            `metadata` (with law_number, article_number) and `content`.
        abolishment_warnings: optional list of human-readable Albanian
            warnings (e.g. "Ligji X/L-Y është shfuqizuar nga..."). Joined
            into the context block so the model includes them in its
            response.
        conversation_history: optional list of OpenAI-format messages from
            previous turns. Inserted between system and current question.
            Capped at 6 messages (3 exchanges) by caller for cost control.
        primary_source_id: if set, the source whose `id` matches gets a
            "BURIMI KRYESOR" label and the prompt instructs the model to
            answer primarily from it (with neighbors as supporting
            context). Used by `citation_lookup` so the model doesn't drift
            to Neni 4 when the user asked Neni 5.

    Returns OpenAI-compatible message list. Order is intentional and
    cache-friendly:
        [system, *history, context+question]
    System and as much of the context as is shared across queries can be
    served from DeepSeek's prompt-prefix cache.
    """
    context_block = _format_sources(sources, primary_source_id=primary_source_id)
    if abolishment_warnings:
        warning_block = "\n\nPARALAJMËRIME PËR SHFUQIZIM:\n" + "\n".join(
            f"- {w}" for w in abolishment_warnings
        )
        context_block += warning_block

    user_payload = (
        f"{CONTEXT_HEADER_SQ}\n\n{context_block}\n\n"
        f"PYETJA: {question}\n\n"
        "Përgjigju në shqip duke ndjekur rregullat e mësipërme."
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT_SQ},
    ]
    if conversation_history:
        messages.extend(conversation_history[-6:])  # last 3 turns
    messages.append({"role": "user", "content": user_payload})
    return messages


def _format_sources(
    sources: list[dict[str, Any]],
    *,
    primary_source_id: str | None = None,
) -> str:
    """Render retrieved sources for the prompt.

    Each source becomes a delimited block headed by its citation key so
    the model can copy `[Neni N, Ligji X/L-Y]` directly from the prompt.
    Empty / null fields are tolerated — we just skip what's missing.

    When `primary_source_id` is provided, the matching source is labeled
    `BURIMI KRYESOR` (primary source) and the others as context, with an
    instruction nudging the model to answer primarily from the primary.
    """
    if not sources:
        return "(Asnjë burim i marrë.)"

    blocks: list[str] = []
    primary_index: int | None = None
    for i, src in enumerate(sources):
        if primary_source_id is not None and src.get("id") == primary_source_id:
            primary_index = i
            break

    for i, src in enumerate(sources, start=1):
        meta = src.get("metadata") or src.get("document_metadata") or {}
        law_number = meta.get("law_number") or "?"
        article_number = meta.get("article_number")
        article_title = meta.get("article_title") or ""
        content = src.get("content") or meta.get("content") or meta.get("text") or ""

        if article_number:
            citation = f"[Neni {article_number}, Ligji {law_number}]"
        else:
            citation = f"[Ligji {law_number}]"

        title_line = f"{citation}"
        if article_title:
            title_line += f" — {article_title}"

        if primary_index is not None and i == primary_index + 1:
            label = f"--- BURIMI KRYESOR (përgjigjja duhet të bazohet kryesisht mbi këtë): {title_line} ---"
        elif primary_index is not None:
            label = f"--- BURIMI MBËSHTETËS {i}: {title_line} ---"
        else:
            label = f"--- BURIMI {i}: {title_line} ---"

        blocks.append(f"{label}\n{content.strip()}")

    return "\n\n".join(blocks)


__all__ = ["SYSTEM_PROMPT_SQ", "build_messages"]
