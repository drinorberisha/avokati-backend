"""
Adapter from `pipeline.AnswerResult` to the `/legal-ai/ask-v2` wire shape.

Lives outside the FastAPI endpoint module so it can be imported and tested
without spinning up the DB / supabase / fastapi stack — important for the
eval harness and for unit tests that stay fast.
"""

from __future__ import annotations

from app.ai.abolishment import AbolishmentRegistry
from app.ai.law_catalog import LawCatalog
from app.schemas.avokai import (
    AskV2Response,
    CitationRecord,
    LlmUsage,
    ScoreBand,
    SourceCard,
)


def score_to_band(score: float, chunk_type: str) -> ScoreBand:
    """Bucket a raw retrieval score into a UI-friendly band.

    The bands are named for end-user understanding ("strong" not ">0.5").
    Citation-lookup and status-lookup chunks get score=1.0 from the pipeline
    by convention (they're metadata-filter hits, not similarity hits), so
    we map those to 'strong' regardless of the numeric value. The
    `synthetic` chunk types from the abolishment registry are also strong
    because they're authoritative answers, not similarity matches.
    """
    if chunk_type in ("status_verdict", "abolisher", "abolished"):
        return "strong"
    if score >= 0.5:
        return "strong"
    if score >= 0.3:
        return "moderate"
    if score >= 0.18:
        return "topical"
    return "weak"


def adapt_pipeline_result_to_v2(
    result,                    # pipeline.AnswerResult
    catalog: LawCatalog | None = None,
    registry: AbolishmentRegistry | None = None,
) -> AskV2Response:
    """Convert a `pipeline.AnswerResult` into the `/ask-v2` wire shape.

    Catalog and registry default to their singletons. Pass explicit
    instances for tests where you want to avoid the on-disk JSON loads.
    """
    catalog = catalog or LawCatalog.get()
    registry = registry or AbolishmentRegistry.get()

    cited_source_ids = {
        c.get("matched_source_id")
        for c in (result.citations or [])
        if c.get("verified") and c.get("matched_source_id")
    }
    primary_source_id = None
    if (
        result.intent == "citation_lookup"
        and result.route_trace.get("citation_match_quality") == "exact_start"
        and result.sources
    ):
        primary_source_id = result.sources[0].get("id")

    cards: list[SourceCard] = []
    for s in result.sources:
        meta = s.get("metadata") or {}
        law = meta.get("law_number") or ""
        chunk_id = s.get("id") or meta.get("chunk_id") or ""
        chunk_type = meta.get("chunk_type") or "article"
        score = float(s.get("score") or 0.0)

        info = registry.lookup(law) if law else None
        is_abolished = bool(info) and info.status in (
            "fully_abolished",
            "partially_abolished",
        )
        abolished_by = (
            [(r.get("abolishing_law") or {}).get("law_number") for r in info.abolished_by]
            if info
            else []
        )
        abolished_by = [x for x in abolished_by if x]

        cat = catalog.lookup(law) if law else None

        cards.append(
            SourceCard(
                id=chunk_id,
                law_number=law or "?",
                law_title=(cat.title if cat else None) or meta.get("law_title"),
                article_number=meta.get("article_number") or None,
                article_title=meta.get("article_title") or None,
                chapter_number=meta.get("chapter_number") or None,
                chapter_title=meta.get("chapter_title") or None,
                chunk_type=chunk_type,
                content=s.get("content") or meta.get("content") or "",
                content_truncated=bool(meta.get("content_truncated")),
                score=score,
                score_band=score_to_band(score, chunk_type),
                is_primary=(chunk_id == primary_source_id),
                is_cited=(chunk_id in cited_source_ids),
                is_abolished=is_abolished,
                abolished_by=abolished_by,
                abolishment_type=(
                    info.status.replace("_abolished", "") if info and is_abolished else None
                ),
                publication_date=cat.publication_date_iso if cat else None,
                gazette_number=cat.gazette_number if cat else None,
                law_url=cat.url if cat else None,
            )
        )

    citations = [
        CitationRecord(
            raw=c["raw"],
            law_number=c["law_number"],
            article_number=c.get("article_number"),
            verified=bool(c.get("verified")),
            matched_source_id=c.get("matched_source_id"),
        )
        for c in (result.citations or [])
    ]

    llm = LlmUsage(**result.llm_usage) if result.llm_usage else None

    return AskV2Response(
        query=result.query,
        answer=result.answer,
        intent=result.intent,
        sources=cards,
        citations=citations,
        abolishment_warnings=list(result.abolishment_warnings or []),
        elapsed_ms=int(result.elapsed_ms),
        llm_usage=llm,
        route_trace=result.route_trace or {},
    )


__all__ = ["adapt_pipeline_result_to_v2", "score_to_band"]
