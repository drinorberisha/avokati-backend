"""One-time migration: copy existing files from AWS S3 to the EU GCS bucket and
rewrite the DB object keys. See docs/COMPLIANCE_PLAN.md Phase 1.

What it does:
  * documents.url      — legacy rows store an (expired) presigned S3 URL; new
                         code stores the object KEY. We derive the S3 key, copy
                         the object to GCS under the same key, and set url=key.
  * library_documents.file_url — already stores the S3 key; copy to GCS (same key).

S3 read: boto3 with the AWS creds in backend/.env.
GCS write: shells out to `gcloud storage cp` so it uses the operator's gcloud
auth (no ADC setup needed). Run from the backend/ dir with the venv python.

Usage:
  venv/bin/python scripts/migrate_s3_to_gcs.py --dry-run   # list what would move
  venv/bin/python scripts/migrate_s3_to_gcs.py             # do it
"""

import argparse
import asyncio
import os
import subprocess
import tempfile
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

load_dotenv(".env")

import asyncpg  # noqa: E402
import boto3  # noqa: E402

S3_BUCKET = "avokati-documents"
GCS_BUCKET = os.environ.get("GCS_BUCKET_NAME", "avokati-documents-eu")


def _s3_key_from(value: str) -> str | None:
    """Return the S3 object key from a stored value that may be a bare key or a
    full (presigned) S3 URL."""
    if not value:
        return None
    if not value.startswith("http"):
        return value.lstrip("/")
    path = urlparse(value).path.lstrip("/")
    # path-style URLs include the bucket as the first segment
    if path.startswith(S3_BUCKET + "/"):
        path = path[len(S3_BUCKET) + 1:]
    return unquote(path) or None


def _copy_to_gcs(s3, key: str) -> bool:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        s3.download_file(S3_BUCKET, key, tmp_path)
        subprocess.run(
            ["gcloud", "storage", "cp", tmp_path, f"gs://{GCS_BUCKET}/{key}"],
            check=True, capture_output=True, text=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ! failed: {key}: {exc}")
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def main(dry_run: bool) -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        docs = await conn.fetch("select id, url from documents where url is not null")
        libs = await conn.fetch("select id, file_url from library_documents where file_url is not null")
        print(f"documents: {len(docs)} | library_documents: {len(libs)}")

        for r in docs:
            key = _s3_key_from(r["url"])
            if not key:
                continue
            print(f"[documents {r['id']}] key={key}")
            if dry_run:
                continue
            if _copy_to_gcs(s3, key):
                await conn.execute("update documents set url=$1 where id=$2", key, r["id"])

        for r in libs:
            key = _s3_key_from(r["file_url"])
            if not key:
                continue
            print(f"[library {r['id']}] key={key}")
            if dry_run:
                continue
            if _copy_to_gcs(s3, key):
                # file_url already equals the key; nothing to update
                pass

        print("DRY RUN — nothing changed." if dry_run else "Migration complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
