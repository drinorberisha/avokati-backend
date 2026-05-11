"""
Generate a golden evaluation set for AvokAI retrieval.

Produces ~40 realistic Albanian legal queries with ground-truth expected
law numbers (and where applicable, article numbers). The set is built
programmatically from the existing scraped law metadata so it is fully
reproducible — no human curation required.

Run:
    python backend/tests/eval/generate_golden_set.py

Output:
    backend/tests/eval/golden_queries.json
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_DIR = REPO_ROOT / "Scraping" / "CategorizationandChunking" / "data" / "processed" / "metadata"
CHUNKS_DIR = REPO_ROOT / "Scraping" / "CategorizationandChunking" / "data" / "processed" / "chunks"
ABOLISHMENT_PATH = REPO_ROOT / "Scraping" / "data" / "abolishment_relations.json"
OUTPUT_PATH = Path(__file__).parent / "golden_queries.json"

SEED = 202605072  # bumped after stricter topic-keyword filter
random.seed(SEED)


# ----- helpers -----------------------------------------------------------

def _load_metadata() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(METADATA_DIR.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                docs.append(json.load(f))
        except Exception:
            continue
    return docs


def _load_chunks(law_number: str) -> list[dict[str, Any]]:
    fname = law_number.replace("/", "_") + "_chunks.json"
    path = CHUNKS_DIR / fname
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f).get("chunks", [])
    except Exception:
        return []


def _good_articles(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Return articles that have a clean, short, real-looking title."""
    out = []
    for art in meta.get("article_structure", []):
        title = (art.get("article_title") or "").strip()
        num = (art.get("article_number") or "").strip()
        if not num or not num.isdigit():
            continue
        # filter title corruption: real titles are short and don't contain "Ligji"/punctuation-heavy junk
        if not title or len(title) > 60 or len(title) < 3:
            continue
        if title.lower().startswith(("ligji", "në ligjin", "neni")):
            continue
        if re.search(r"\d{4}/\d", title):
            continue
        out.append({"article_number": num, "article_title": title})
    return out


def _topic_keywords(meta: dict[str, Any]) -> list[str]:
    """Pick keywords that are reasonable topic descriptors (multi-syllable, not stopwords).

    Stricter than the first pass — the original set produced "pastaj" / "eshte" /
    "kosovës" as topics, which are too common or too generic to test retrieval
    meaningfully. We require:
      - len ≥ 6 (rules out short common Albanian words)
      - not in an expanded stoplist
      - not a generic place name or pronoun-shaped form
      - not a verb stem we've seen come through (eshte=is, pastaj=after)
    """
    stops = {
        # Articles / pronouns / clitics
        "neni", "ligji", "ligjit", "ligjin", "këtij", "kësaj", "është", "eshte",
        "mund", "kjo", "ky", "këto", "këta", "atë", "atij", "asaj",
        "këtu", "ketu", "këtë", "kete",
        # Conjunctions / prepositions
        "për", "per", "nga", "deri", "mbi", "para", "pasi", "pastaj",
        # Generic verbs
        "thotë", "thote", "flet", "rregullon",
        # Place names that aren't selective enough as a sole topic
        "kosovës", "kosoves", "kosova", "republikës", "republikes",
        # Generic nouns that produce ambiguous queries
        "personal", "organi", "pjesa", "rasti", "rastin",
    }
    kws = []
    for kw in meta.get("keywords", []):
        kw = (kw or "").strip().lower()
        if len(kw) < 6 or kw in stops:
            continue
        # Drop pure-Latin alphabetic content that's just a citation fragment
        kws.append(kw)
    return kws


# ----- query generators --------------------------------------------------

DIRECT_CITATION_TEMPLATES = [
    "Çfarë thotë Neni {n} i Ligjit {law}?",
    "Çfarë përcakton Neni {n} i ligjit {law}?",
    "Më trego përmbajtjen e Nenit {n} të Ligjit {law}.",
    "Cila është dispozita e Nenit {n} në Ligjin {law}?",
    "Sipas Ligjit {law}, Neni {n}, çfarë rregullohet?",
]

CITATION_BY_TITLE_TEMPLATES = [
    "Çfarë thotë Neni mbi {topic} në Ligjin {law}?",
    "Cila është dispozita për {topic} në Ligjin {law}?",
]

TOPIC_TEMPLATES = [
    "Cilat ligje rregullojnë {topic}?",
    "Cili ligj flet për {topic}?",
    "Cilat janë dispozitat ligjore për {topic}?",
    "Më trego ligjin që rregullon {topic}.",
    "Çfarë thotë legjislacioni për {topic}?",
]

STATUS_TEMPLATES = [
    "A është aktiv Ligji {law}?",
    "A është abroguar Ligji {law}?",
    "Cili ligj e ka zëvendësuar Ligjin {law}?",
    "A është ende në fuqi Ligji {law}?",
]


def gen_direct_citation(docs: list[dict[str, Any]], n: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    candidates = [d for d in docs if _good_articles(d)]
    random.shuffle(candidates)
    for meta in candidates:
        if len(out) >= n:
            break
        arts = _good_articles(meta)
        art = random.choice(arts)
        tmpl = random.choice(DIRECT_CITATION_TEMPLATES)
        q = tmpl.format(n=art["article_number"], law=meta["law_number"])
        out.append({
            "category": "direct_citation",
            "query": q,
            "expected_law_numbers": [meta["law_number"]],
            "expected_article_numbers": [art["article_number"]],
            "notes": f"Article title: {art['article_title']}",
        })
    return out


def gen_topic(docs: list[dict[str, Any]], n: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    candidates = [d for d in docs if _topic_keywords(d)]
    random.shuffle(candidates)
    for meta in candidates:
        if len(out) >= n:
            break
        kws = _topic_keywords(meta)
        topic = random.choice(kws)
        tmpl = random.choice(TOPIC_TEMPLATES)
        q = tmpl.format(topic=topic)
        # Ground truth: any law whose keywords contain this topic.
        # For programmatic tractability we accept the source law as the primary
        # expected hit, but also collect any others that share the keyword.
        expected = [meta["law_number"]]
        for other in docs:
            if other is meta:
                continue
            if topic in [k.lower() for k in other.get("keywords", [])]:
                expected.append(other["law_number"])
        out.append({
            "category": "topic",
            "query": q,
            "expected_law_numbers": expected[:5],  # cap to avoid noise
            "expected_article_numbers": None,
            "notes": f"Topic keyword: {topic}",
        })
    return out


def gen_article_content(docs: list[dict[str, Any]], n: int = 8) -> list[dict[str, Any]]:
    """Generate semantic queries from real article phrases.

    Skips chunks whose body shows the legacy `/uni04xx` corruption — those
    on-disk chunks are PyPDF2 artifacts that don't exist in the v2 index, so
    using them as queries produces unanswerable questions that don't reflect
    real-world performance.
    """
    out: list[dict[str, Any]] = []
    candidates = list(docs)
    random.shuffle(candidates)
    for meta in candidates:
        if len(out) >= n:
            break
        chunks = _load_chunks(meta["law_number"])
        article_chunks = [c for c in chunks if c.get("chunk_type") == "article" and c.get("article_number", "").isdigit()]
        if not article_chunks:
            continue
        chunk = random.choice(article_chunks)
        content = (chunk.get("content") or "").strip()
        # Reject corrupted source — content with /uni0xxx artifacts won't match
        # anything in v2 since v2 was extracted with PyMuPDF (clean diacritics).
        if "/uni0" in content:
            continue
        # Take a substantive phrase from the body (skip "Neni N" header line)
        lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
        body_lines = [
            ln for ln in lines
            if not re.match(r"^Neni\s+\d", ln, re.IGNORECASE) and len(ln) > 30
        ]
        if not body_lines:
            continue
        # Skip lines that look like pure references ("Pas nenit 4 të ligjit bazik...")
        # These are amendment-law preambles that don't carry semantic content
        # describing the topic — they describe the amendment mechanic.
        sentence = next(
            (ln for ln in body_lines if not re.search(r"\bligjit?\s+bazi[kc]\b", ln, re.IGNORECASE)),
            body_lines[0],
        )
        sentence = sentence[:120].rstrip(".,;: ")
        q = f"Çfarë thotë ligji për: {sentence}?"
        out.append({
            "category": "article_content",
            "query": q,
            "expected_law_numbers": [meta["law_number"]],
            "expected_article_numbers": [chunk.get("article_number")],
            "notes": "Generated from article body excerpt",
        })
    return out


def gen_status(n: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not ABOLISHMENT_PATH.exists():
        return out
    with ABOLISHMENT_PATH.open(encoding="utf-8") as f:
        rels = json.load(f)
    random.shuffle(rels)
    for rel in rels:
        if len(out) >= n:
            break
        abolished = (rel.get("abolished_law") or {}).get("law_number")
        abolisher = (rel.get("abolishing_law") or {}).get("law_number")
        if not abolished or not abolisher:
            continue
        tmpl = random.choice(STATUS_TEMPLATES)
        q = tmpl.format(law=abolished)
        # For status queries, the relevant retrieval target is the abolished law
        # and/or the abolishing law (so the system has enough context to answer).
        out.append({
            "category": "status",
            "query": q,
            "expected_law_numbers": [abolished, abolisher],
            "expected_article_numbers": None,
            "notes": f"Abolished by {abolisher} ({rel.get('abolishment_type')})",
        })
    return out


# ----- main --------------------------------------------------------------

def main() -> None:
    if not METADATA_DIR.exists():
        raise SystemExit(f"Metadata dir not found: {METADATA_DIR}")

    docs = _load_metadata()
    print(f"Loaded {len(docs)} law metadata files")

    queries: list[dict[str, Any]] = []
    queries += gen_direct_citation(docs, n=12)
    queries += gen_topic(docs, n=12)
    queries += gen_article_content(docs, n=8)
    queries += gen_status(n=8)

    # Stable IDs and shuffle once for run-order randomization
    random.shuffle(queries)
    for i, q in enumerate(queries, start=1):
        q["id"] = f"q{i:03d}"

    payload = {
        "version": 1,
        "seed": SEED,
        "total": len(queries),
        "by_category": {},
        "queries": queries,
    }
    for q in queries:
        payload["by_category"][q["category"]] = payload["by_category"].get(q["category"], 0) + 1

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(queries)} queries to {OUTPUT_PATH}")
    for cat, count in payload["by_category"].items():
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
