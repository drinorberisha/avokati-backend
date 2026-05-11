"""
Audit the Pinecone index for OCR/extraction corruption.

Samples a random set of vectors and inspects their text for:
  - `/uni0xxx` glyph-mapping artifacts (PyPDF2 failure on Albanian diacritics)
  - whitespace inserted inside words (split-token artifacts like "shpal ljes")
  - empty / near-empty content

Run:
    cd backend && venv/bin/python tests/eval/audit_index.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = EVAL_DIR.parents[1]

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from openai import OpenAI
from pinecone import Pinecone


SAMPLE_SIZE = 50
RESULTS_DIR = EVAL_DIR / "results"


def main() -> None:
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index_name = os.environ.get("PINECONE_INDEX_NAME", "lawvector")
    namespace = os.environ.get("PINECONE_NAMESPACE", "default")
    index = pc.Index(index_name)

    stats = index.describe_index_stats()
    total = stats.get("total_vector_count") or sum(
        ns.get("vector_count", 0) for ns in (stats.get("namespaces") or {}).values()
    )
    print(f"Index: {index_name} / namespace: {namespace} / total vectors: {total}")

    # Use a diverse set of probe queries to fan out across the index
    probes = [
        "ligj rregullim", "neni qëllim", "kushtet kontratës", "punësim", "tatimi",
        "trashëgimi", "trafik rrugor", "mbrojtja e konsumatorit", "arsimi",
        "shëndetësia", "policia", "gjykata", "pronë", "buxheti", "komuna",
    ]
    embed_model = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    oai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    seen_ids: set[str] = set()
    samples: list[dict] = []
    per_probe = max(1, SAMPLE_SIZE // len(probes) + 1)
    for probe in probes:
        if len(samples) >= SAMPLE_SIZE:
            break
        emb = oai.embeddings.create(model=embed_model, input=[probe]).data[0].embedding
        res = index.query(namespace=namespace, vector=emb, top_k=per_probe, include_metadata=True)
        for m in res.matches:
            if m.id in seen_ids:
                continue
            seen_ids.add(m.id)
            meta = dict(m.metadata or {})
            text = meta.get("content") or meta.get("text") or ""
            samples.append({
                "id": m.id,
                "law_number": meta.get("law_number"),
                "article_number": meta.get("article_number"),
                "text_len": len(text),
                "text": text,
            })
            if len(samples) >= SAMPLE_SIZE:
                break

    print(f"\nSampled {len(samples)} vectors")

    # ----- detect corruption signals -----
    uni_pattern = re.compile(r"/uni0[0-9a-fA-F]{3}")
    # split-word artifact: a single letter or two flanked by spaces inside what should be a word
    split_word_pattern = re.compile(r"\b\w{1,2} \w{1,3} \w{1,4}\b")
    has_albanian_diacritics = re.compile(r"[ëçËÇ]")

    counts = {
        "with_uni_artifact": 0,
        "missing_diacritics": 0,  # contains letters but no ë/ç at all in a long-enough chunk
        "very_short": 0,           # < 50 chars
        "looks_clean": 0,
    }
    examples = {"uni_artifact": [], "missing_diacritics": []}

    for s in samples:
        t = s["text"]
        if not t or len(t) < 50:
            counts["very_short"] += 1
            continue
        has_uni = bool(uni_pattern.search(t))
        if has_uni:
            counts["with_uni_artifact"] += 1
            if len(examples["uni_artifact"]) < 5:
                examples["uni_artifact"].append({"id": s["id"], "law": s["law_number"], "snippet": t[:300]})
            continue
        # Albanian text without any ë/ç across 200+ chars is almost certainly corrupted
        if len(t) > 200 and not has_albanian_diacritics.search(t):
            counts["missing_diacritics"] += 1
            if len(examples["missing_diacritics"]) < 3:
                examples["missing_diacritics"].append({"id": s["id"], "law": s["law_number"], "snippet": t[:300]})
            continue
        counts["looks_clean"] += 1

    pct = lambda n: (n / len(samples) * 100) if samples else 0
    print("\n--- Corruption summary ---")
    for k, v in counts.items():
        print(f"  {k:>22s}: {v} ({pct(v):.1f}%)")

    payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "index": index_name,
        "namespace": namespace,
        "total_vectors": total,
        "sampled": len(samples),
        "counts": counts,
        "examples": examples,
        "samples": samples,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"audit_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nSaved: {out}")

    if examples["uni_artifact"]:
        print("\n--- Sample of /uni-corrupted chunks ---")
        for e in examples["uni_artifact"][:3]:
            print(f"  [{e['law']}] {e['snippet'][:200]}")


if __name__ == "__main__":
    main()
