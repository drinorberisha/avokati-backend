"""Office document library (AvokAI "Documents" tab).

Simple per-office storage of uploaded legal documents: the file goes to S3
(private bucket, served via presigned URLs), the metadata to Postgres. Not
indexed into Pinecone — this does not affect AvokAI answers.
"""

import io
import logging
import uuid
from datetime import datetime
from typing import Any, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.s3 import s3
from app.core.supabase import get_supabase_client
from app.core.tenancy import require_office
from app.schemas.library import LibraryDocumentOut, LibraryDownloadOut
from app.schemas.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

_FIELDS = "id, title, document_type, file_name, file_size, mime_type, created_at"


def _normalize(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "document_type": row.get("document_type"),
        "file_name": row.get("file_name"),
        "file_size": row.get("file_size"),
        "mime_type": row.get("mime_type"),
        "created_at": row.get("created_at"),
    }


def _file_key(office_id: str, filename: str) -> str:
    now = datetime.now()
    return f"library/{office_id}/{now.year}/{now.month:02d}/{uuid.uuid4()}_{filename}"


@router.get("/", response_model=List[LibraryDocumentOut])
async def list_documents(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("library_documents")
        .select(_FIELDS)
        .eq("office_id", office_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_normalize(row) for row in resp.data or []]


@router.post("/upload", response_model=LibraryDocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    *,
    file: UploadFile = File(...),
    title: str = Form(...),
    document_type: str = Form("other"),
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    content_type = file.content_type or "application/octet-stream"
    if content_type not in settings.ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type {content_type} not allowed. Allowed: {', '.join(settings.ALLOWED_UPLOAD_TYPES)}",
        )

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds the {settings.MAX_UPLOAD_SIZE // (1024 * 1024)}MB limit.",
        )

    file_key = _file_key(office_id, file.filename or "document")
    uploaded = await s3.upload_file(io.BytesIO(content), file_key, content_type=content_type)
    if not uploaded:
        raise HTTPException(status_code=500, detail="Failed to upload the file.")

    record = {
        "office_id": office_id,
        "owner_id": str(current_user.id),
        "title": title,
        "document_type": document_type,
        "file_name": file.filename,
        "file_url": file_key,
        "file_size": len(content),
        "mime_type": content_type,
    }
    resp = supabase.table("library_documents").insert(record).execute()
    if not resp.data:
        # roll back the orphaned S3 object
        try:
            await s3.delete_file(file_key)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=400, detail="Failed to save the document.")
    return _normalize(resp.data[0])


@router.get("/{document_id}/download", response_model=LibraryDownloadOut)
async def download_document(
    *,
    document_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("library_documents")
        .select("id, file_url")
        .eq("id", document_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Document not found")
    url = await s3.generate_presigned_url(resp.data[0]["file_url"], "get_object", expiration=3600)
    if not url:
        raise HTTPException(status_code=500, detail="Could not generate a download link.")
    return {"url": url}


@router.delete("/{document_id}", status_code=status.HTTP_200_OK)
async def delete_document(
    *,
    document_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = (
        supabase.table("library_documents")
        .select("id, file_url")
        .eq("id", document_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        await s3.delete_file(resp.data[0]["file_url"])
    except Exception as exc:  # noqa: BLE001 - DB row is the source of truth
        logger.warning("S3 delete failed for %s: %s", resp.data[0]["file_url"], exc)

    supabase.table("library_documents").delete().eq("id", document_id).eq("office_id", office_id).execute()
    return {"success": True}


@router.delete("/", status_code=status.HTTP_200_OK)
async def delete_all_documents(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    resp = supabase.table("library_documents").select("file_url").eq("office_id", office_id).execute()
    for row in resp.data or []:
        try:
            await s3.delete_file(row["file_url"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("S3 delete failed for %s: %s", row["file_url"], exc)
    supabase.table("library_documents").delete().eq("office_id", office_id).execute()
    return {"success": True, "deleted": len(resp.data or [])}
