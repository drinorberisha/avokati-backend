"""
Build the v2 Pinecone namespace from the on-disk law PDFs.

Pipeline per law:
  1. PyMuPDF extracts clean Albanian text from the PDF.
  2. v2_chunker splits into per-article chunks with real metadata
     (`law_number`, `article_number`, `article_title`, `chapter_number`).
  3. OpenAI text-embedding-3-large embeds each article.
  4. Pinecone upsert into namespace `default_v2`.

Idempotent: chunks have stable IDs (`{law_safe}_art{N}`), so re-running
skips already-upserted chunks. Resume after interrupt is built-in.

Non-destructive: writes ONLY to `default_v2`. Never touches the live
`default` namespace. The cutover is a separate, deliberate operation
done by changing the backend's `PINECONE_NAMESPACE` env var.

Usage:
    cd backend && venv/bin/python scripts/build_v2_index.py --limit 20
    cd backend && venv/bin/python scripts/build_v2_index.py             # full run
    cd backend && venv/bin/python scripts/build_v2_index.py --dry-run   # extract+chunk only

Costs: ~10M tokens × $0.13/M = ~$1.30 to embed all 1003 laws once.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from app.ai.v2_chunker import LegalChunk, chunk_law  # noqa: E402

REPO_ROOT = BACKEND_ROOT.parent
LAWS_DIR = REPO_ROOT / "Scraping" / "data" / "laws"
V2_NAMESPACE = "default_v2"
EMBED_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
EMBED_BATCH = 50          # OpenAI handles up to 2048 inputs but we want bounded latency
UPSERT_BATCH = 100        # Pinecone recommended batch size

STATE_PATH = BACKEND_ROOT / "tests" / "eval" / "results" / f"v2_index_state.json"


# ----- helpers -----------------------------------------------------------

def _law_number_from_dirname(dirname: str) -> str:
    """`02_L-10` → `02/L-10`. The dir name is the canonical form with `_` for `/`."""
    return dirname.replace("_", "/", 1)


def _extract_text(pdf_path: Path) -> str:
    """PyMuPDF text extraction. Returns empty string on failure."""
    import fitz
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        print(f"    [extract] failed: {e}")
        return ""
    try:
        return "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()


def _looks_scanned(text: str) -> bool:
    """Detect mostly-image PDFs that PyMuPDF can't read."""
    return len(text.strip()) < 500


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        with STATE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {"completed_laws": [], "skipped_laws": {}, "total_chunks_upserted": 0}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _build_clients():
    from openai import OpenAI
    from pinecone import Pinecone

    oai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index_name = os.environ.get("PINECONE_INDEX_NAME", "lawvector")
    index = pc.Index(index_name)
    return oai, index


def _batches(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ----- main --------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="Process only the first N laws")
    p.add_argument("--dry-run", action="store_true", help="Extract + chunk but skip embed/upsert")
    p.add_argument("--reset", action="store_true", help="Wipe the v2 namespace before starting (DESTRUCTIVE within v2 only)")
    p.add_argument("--namespace", default=V2_NAMESPACE, help="Target namespace (default: default_v2)")
    p.add_argument("--law", action="append", help="Process only specific law dirs (can repeat). E.g. --law 02_L-10")
    args = p.parse_args()

    if not LAWS_DIR.exists():
        raise SystemExit(f"Laws dir not found: {LAWS_DIR}")

    state = _load_state()
    completed = set(state["completed_laws"])

    # Pick laws to process
    if args.law:
        law_dirs = [LAWS_DIR / d for d in args.law]
        for d in law_dirs:
            if not d.exists():
                raise SystemExit(f"Specified law dir not found: {d}")
    else:
        law_dirs = sorted(d for d in LAWS_DIR.iterdir() if d.is_dir())
        if args.limit is not None:
            law_dirs = law_dirs[:args.limit]

    print(f"Target namespace: {args.namespace}")
    print(f"Laws to process: {len(law_dirs)} (already completed: {len(completed)})")
    print(f"Dry run: {args.dry_run}")
    print()

    oai = index = None
    if not args.dry_run:
        oai, index = _build_clients()
        if args.reset:
            print(f"[reset] DELETING all vectors in namespace {args.namespace!r}...")
            index.delete(delete_all=True, namespace=args.namespace)
            state = {"completed_laws": [], "skipped_laws": {}, "total_chunks_upserted": 0}
            completed = set()
            _save_state(state)
            time.sleep(2)

    total_chunks = 0
    total_skipped = 0
    t_start = time.time()

    for i, law_dir in enumerate(law_dirs, start=1):
        law_number = _law_number_from_dirname(law_dir.name)
        if law_number in completed:
            # Idempotent skip — even with --law, don't redo work that's done.
            # If the user really wants to redo a law, they can --reset or
            # delete its IDs separately.
            continue

        # Find the PDF
        pdfs = sorted(law_dir.glob("*.pdf"))
        if not pdfs:
            state["skipped_laws"][law_number] = "no_pdf"
            total_skipped += 1
            continue

        print(f"[{i}/{len(law_dirs)}] {law_number}")
        t0 = time.time()

        # Concatenate text from all PDFs in the directory (rare — usually 1)
        text = "\n".join(_extract_text(pdf) for pdf in pdfs)
        if _looks_scanned(text):
            state["skipped_laws"][law_number] = "scanned_or_empty"
            print(f"    skip: PyMuPDF returned {len(text)} chars (likely scanned)")
            total_skipped += 1
            _save_state(state)
            continue

        chunks = chunk_law(law_number, text)
        if not chunks:
            state["skipped_laws"][law_number] = "no_articles_detected"
            print(f"    skip: no Neni boundaries found in {len(text)} chars")
            total_skipped += 1
            _save_state(state)
            continue

        print(f"    extracted {len(text):>6} chars → {len(chunks):>3} article chunks ({(time.time()-t0)*1000:.0f}ms)")

        if args.dry_run:
            for c in chunks[:2]:
                print(f"      • Neni {c.article_number}: {c.article_title!r} ({len(c.content)} chars)")
            continue

        # Embed in batches. Defensive: hard-truncate any input to a token-
        # safe character cap. The 8192-token embedding limit is the wall.
        # We aim well below it. The full original content is already
        # preserved in chunk.content (and thus in Pinecone metadata), so
        # embedding on a head-truncated form is an acceptable compromise.
        EMBED_INPUT_CAP = 10_000  # ~4K tokens worst-case
        contents = [c.content[:EMBED_INPUT_CAP] for c in chunks]
        embeddings: list[list[float]] = []
        for batch in _batches(contents, EMBED_BATCH):
            try:
                resp = oai.embeddings.create(model=EMBED_MODEL, input=batch)
                embeddings.extend(item.embedding for item in resp.data)
            except Exception as e:
                # Last-resort defense: re-truncate harder and retry once
                print(f"    [embed] batch failed ({e}); retrying with shorter inputs")
                shorter = [c[:5000] for c in batch]
                resp = oai.embeddings.create(model=EMBED_MODEL, input=shorter)
                embeddings.extend(item.embedding for item in resp.data)

        # Build Pinecone vectors and upsert
        vectors = [
            {"id": c.chunk_id, "values": emb, "metadata": c.to_pinecone_metadata()}
            for c, emb in zip(chunks, embeddings)
        ]
        for batch in _batches(vectors, UPSERT_BATCH):
            index.upsert(vectors=batch, namespace=args.namespace)

        total_chunks += len(chunks)
        completed.add(law_number)
        state["completed_laws"] = sorted(completed)
        state["total_chunks_upserted"] = state.get("total_chunks_upserted", 0) + len(chunks)
        _save_state(state)
        print(f"    upserted {len(chunks)} chunks ({(time.time()-t0)*1000:.0f}ms total)")

    dur = time.time() - t_start
    print(f"\nDone in {dur:.1f}s.")
    print(f"  Laws completed: {len(completed) - len(state.get('completed_laws', [])) + len(completed)}")
    print(f"  Chunks upserted this run: {total_chunks}")
    print(f"  Skipped: {total_skipped}")
    print(f"  Total in v2 namespace (cumulative): {state.get('total_chunks_upserted', 0)}")
    print(f"  State: {STATE_PATH}")


if __name__ == "__main__":
    main()
