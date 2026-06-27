"""
Re-ingest one (or a few) already-indexed laws into the live v2 namespace.

Use this when a chunker fix changes how an ALREADY-completed law splits into
articles — the build script's idempotent ID-based upsert can't remove chunks
whose IDs no longer exist (e.g. an old `_art999_p0` mega-chunk that the fix
replaces with `_art999` + `_art1000`...). This script handles that safely:

  1. Snapshot the law's current chunk IDs in the index (filter query).
  2. Re-chunk with the CURRENT chunker, embed, and upsert (overwrites the
     articles that still exist, adds the newly-captured ones).
  3. Delete only the now-stale IDs (old minus new). The upsert runs BEFORE
     the delete, so the law is never absent from the live index — no query
     window where 04/L-077 returns nothing.

Usage:
    cd backend && venv/bin/python scripts/reingest_law.py --law 04_L-077
    cd backend && venv/bin/python scripts/reingest_law.py --law 04_L-077 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from app.ai.v2_chunker import chunk_law  # noqa: E402
from scripts.build_v2_index import (  # noqa: E402
    EMBED_MODEL,
    UPSERT_BATCH,
    EMBED_BATCH,
    LAWS_DIR,
    V2_NAMESPACE,
    STATE_PATH,
    _batches,
    _build_clients,
    _extract_text,
    _law_number_from_dirname,
    _load_state,
    _save_state,
    _looks_scanned,
)

import os  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402


_ART_IN_ID = re.compile(r"_art(\d+)")


def _article_numbers(ids: list[str]) -> set[str]:
    """The set of article numbers a list of chunk_ids covers.

    Robust to the `_d{section}` and `_p{n}` affixes, so a re-section or a
    paragraph re-split doesn't read as a different article — only a genuinely
    dropped article changes this set.
    """
    return {m.group(1) for i in ids if (m := _ART_IN_ID.search(i))}


def _existing_ids(index, namespace: str, law_number: str, dummy_vec: list[float]) -> list[str]:
    """All chunk IDs currently in the index for this law_number."""
    res = index.query(
        namespace=namespace,
        vector=dummy_vec,
        top_k=10_000,
        include_metadata=False,
        filter={"law_number": law_number},
    )
    return [m.id for m in res.matches]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--law", action="append", required=True,
                   help="Law dir name(s), e.g. 04_L-077 (repeatable)")
    p.add_argument("--namespace", default=os.environ.get("PINECONE_NAMESPACE_V2", V2_NAMESPACE))
    p.add_argument("--dry-run", action="store_true",
                   help="Chunk + report the upsert/delete plan; touch nothing.")
    p.add_argument("--ocr", action="store_true",
                   help="Extract text via Gemini OCR instead of PyMuPDF — for laws whose "
                        "text layer is garbled (broken font encoding). See app/ai/ocr.py. "
                        "By default OCRs ONLY the garbled pages (page-selective) and keeps "
                        "clean pages' exact PyMuPDF text.")
    p.add_argument("--ocr-full", action="store_true",
                   help="With --ocr, OCR every page (not just the garbled ones). Escape hatch "
                        "for laws where page-selective leaves residual garble at page seams.")
    p.add_argument("--force", action="store_true",
                   help="Re-ingest even if the OCR validation gate fails (still garbled / "
                        "lost articles). Use only after manual review.")
    p.add_argument("--text-file",
                   help="Ingest from a pre-extracted text file instead of OCR/PyMuPDF "
                        "(e.g. an OCR result already saved + reviewed). Requires a single "
                        "--law; the OCR validation gate still applies.")
    args = p.parse_args()
    if args.text_file and len(args.law) != 1:
        raise SystemExit("--text-file requires exactly one --law")

    oai, index = _build_clients()
    ns = args.namespace
    dummy_vec = oai.embeddings.create(model=EMBED_MODEL, input=["placeholder"]).data[0].embedding

    state = _load_state()

    for law_dir_name in args.law:
        law_dir = LAWS_DIR / law_dir_name
        if not law_dir.exists():
            raise SystemExit(f"Law dir not found: {law_dir}")
        law_number = _law_number_from_dirname(law_dir_name)

        pdfs = sorted(law_dir.glob("*.pdf"))
        if not pdfs:
            raise SystemExit(f"No PDF in {law_dir}")
        if args.text_file:
            print(f"  [{law_number}] ingesting from text file {args.text_file}")
            text = Path(args.text_file).read_text(encoding="utf-8")
        elif args.ocr:
            from app.ai.ocr import ocr_pdf_text
            mode = "full" if args.ocr_full else "page-selective"
            print(f"  [{law_number}] OCR via Gemini ({len(pdfs)} pdf, {mode})…")
            text = "\n".join(
                ocr_pdf_text(str(pdf), page_selective=not args.ocr_full) for pdf in pdfs
            )
        else:
            text = "\n".join(_extract_text(pdf) for pdf in pdfs)
        if _looks_scanned(text):
            raise SystemExit(f"{law_number}: extraction looks scanned/empty ({len(text)} chars)")

        chunks = chunk_law(law_number, text)
        if not chunks:
            raise SystemExit(f"{law_number}: chunker produced 0 chunks — refusing to wipe live data")

        new_ids = [c.chunk_id for c in chunks]
        old_ids = _existing_ids(index, ns, law_number, dummy_vec)
        stale = sorted(set(old_ids) - set(new_ids))

        # OCR validation gate: Gemini OCR is great but not perfectly faithful
        # (it can leave residual garble or drop an article body). Refuse to
        # re-ingest a suspicious result unless --force, so the batch is safe.
        if (args.ocr or args.text_file) and not args.dry_run:
            from scripts.audit_garbled_chunks import garble_ratio
            gr = garble_ratio(text)
            dup = len(new_ids) != len(set(new_ids))
            # "Lost content" must be robust to benign re-chunking: a long article
            # split into 2 vs 3 paragraph-parts, or sub-documents re-sectioned, is
            # NOT a loss. So check (a) every article number indexed before is still
            # present, and (b) the OCR text isn't grossly shorter than the raw PDF
            # text (catches OCR dropping a page/body) — not the raw chunk count.
            missing = sorted(
                _article_numbers(old_ids) - _article_numbers(new_ids),
                key=lambda s: (len(s), s),
            )
            raw_len = sum(len(_extract_text(p)) for p in pdfs)
            short = bool(raw_len) and len(text) < 0.80 * raw_len
            if (gr > 0.03 or dup or missing or short) and not args.force:
                print(f"  [{law_number}] GATE FAILED — garble={gr:.2f} dup_ids={dup} "
                      f"missing_articles={missing[:8]} short={short} "
                      f"(ocr {len(text)} vs raw {raw_len} chars; chunks "
                      f"{len(old_ids)}→{len(new_ids)}). Skipping (review + --force).")
                continue

        print(f"\n=== {law_number} (namespace {ns}) ===")
        print(f"  live chunks now : {len(old_ids)}")
        print(f"  new chunks      : {len(new_ids)}")
        print(f"  will overwrite  : {len(set(old_ids) & set(new_ids))}")
        print(f"  net new IDs     : {len(set(new_ids) - set(old_ids))}")
        print(f"  stale to delete : {len(stale)}  {stale[:6]}{'...' if len(stale) > 6 else ''}")

        if args.dry_run:
            print("  [dry-run] no changes made")
            continue

        # 1. Embed (head-truncate defensively, same cap as the build script).
        EMBED_INPUT_CAP = 10_000
        contents = [c.content[:EMBED_INPUT_CAP] for c in chunks]
        embeddings: list[list[float]] = []
        for batch in _batches(contents, EMBED_BATCH):
            resp = oai.embeddings.create(model=EMBED_MODEL, input=batch)
            embeddings.extend(item.embedding for item in resp.data)

        # 2. Upsert new chunks FIRST (no query gap for this law).
        vectors = [
            {"id": c.chunk_id, "values": emb, "metadata": c.to_pinecone_metadata()}
            for c, emb in zip(chunks, embeddings)
        ]
        for batch in _batches(vectors, UPSERT_BATCH):
            index.upsert(vectors=batch, namespace=ns)
        print(f"  upserted {len(vectors)} chunks")

        # 3. Delete only the now-stale IDs.
        if stale:
            for batch in _batches(stale, 1000):
                index.delete(ids=batch, namespace=ns)
            print(f"  deleted {len(stale)} stale chunks")

        # 4. Keep the state file's cumulative count honest.
        delta = len(new_ids) - len(old_ids)
        state["total_chunks_upserted"] = state.get("total_chunks_upserted", 0) + delta
        if law_number not in state.get("completed_laws", []):
            state.setdefault("completed_laws", []).append(law_number)
            state["completed_laws"] = sorted(state["completed_laws"])
        _save_state(state)
        print(f"  state updated (total delta {delta:+d})")


if __name__ == "__main__":
    main()
