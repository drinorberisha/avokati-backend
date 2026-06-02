from datetime import datetime
from typing import Any, List, Optional
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.auth import get_current_user
from app.core.gcs import gcs
from app.core.supabase import get_supabase_client
from app.core.tenancy import require_office, assert_in_office
from app.schemas.document import Document, DocumentCategory, DocumentUpdate
from app.schemas.user import User

router = APIRouter()


def _doc_fields(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "client_id": row.get("client_id"),
        "case_id": row.get("case_id"),
        "description": row.get("description"),
        "created_at": row.get("created_at"),
    }


async def _resolve_url(stored: Optional[str]) -> Optional[str]:
    """The `url` column stores the GCS object key going forward; sign it on read.
    Legacy rows may still hold a full http(s) URL — return those unchanged."""
    if not stored:
        return stored
    if stored.startswith("http"):
        return stored
    return await gcs.generate_signed_url(stored, "get_object", expiration=3600) or stored


async def _normalize_document(row: dict) -> dict:
    return {**_doc_fields(row), "url": await _resolve_url(row.get("url"))}


@router.get("/upload-url")
async def get_upload_url(
    file_name: str,
    content_type: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
) -> Any:
    file_key = gcs.generate_file_key(file_name, prefix="documents", scope_id=office_id)
    upload_url = await gcs.generate_signed_url(file_key, "put_object", content_type=content_type)
    return {"uploadUrl": upload_url, "fileKey": file_key}


@router.post("/", response_model=Document, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: UploadFile = File(...),
    data: str = Form(...),
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = json.loads(data)
    case_id = payload.get("case_id")
    client_id = payload.get("client_id")
    if bool(case_id) == bool(client_id):
        raise HTTPException(status_code=400, detail="Document must be associated with exactly one client or one case")

    # The referenced parent must belong to the caller's office.
    if case_id:
        assert_in_office(supabase, "cases", case_id, office_id, detail="Case not found")
    else:
        assert_in_office(supabase, "clients", client_id, office_id, detail="Client not found")

    file_key = gcs.generate_file_key(file.filename, prefix="documents", scope_id=office_id)
    uploaded = await gcs.upload_file(file.file, file_key, content_type=file.content_type)
    if not uploaded:
        raise HTTPException(status_code=500, detail="Failed to upload file")

    document = {
        "name": payload["name"],
        "category": payload["category"],
        "client_id": client_id,
        "case_id": case_id,
        "description": payload.get("description"),
        "url": file_key,  # store the object key; sign on read
        "office_id": office_id,
        "created_at": datetime.utcnow().isoformat(),
    }
    response = supabase.table("documents").insert(document).execute()
    if not response.data:
        await gcs.delete_file(file_key)
        raise HTTPException(status_code=400, detail="Failed to create document")
    return await _normalize_document(response.data[0])


@router.get("/", response_model=List[Document])
async def get_documents(
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = (
        supabase.table("documents")
        .select("id, name, category, client_id, case_id, description, url, created_at")
        .eq("office_id", office_id)
        .execute()
    )
    return [await _normalize_document(row) for row in response.data or []]


@router.get("/{document_id}", response_model=Document)
async def read_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = (
        supabase.table("documents")
        .select("id, name, category, client_id, case_id, description, url, created_at")
        .eq("id", document_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return await _normalize_document(response.data[0])


@router.put("/{document_id}", response_model=Document)
async def update_document(
    document_id: str,
    file: Optional[UploadFile] = File(None),
    data: str = Form(...),
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = json.loads(data)
    update_data = DocumentUpdate(**payload).model_dump(mode="json", exclude_unset=True)
    update_data.pop("office_id", None)

    if "category" in update_data:
        update_data["category"] = DocumentCategory(update_data["category"]).value
    if file:
        file_key = gcs.generate_file_key(file.filename, prefix="documents", scope_id=office_id)
        uploaded = await gcs.upload_file(file.file, file_key, content_type=file.content_type)
        if not uploaded:
            raise HTTPException(status_code=500, detail="Failed to upload file")
        update_data["url"] = file_key

    response = (
        supabase.table("documents")
        .update(update_data)
        .eq("id", document_id)
        .eq("office_id", office_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return await _normalize_document(response.data[0])


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    office_id: str = Depends(require_office),
    supabase=Depends(get_supabase_client),
) -> Any:
    # Fetch the object key first so we can remove the file too.
    existing = (
        supabase.table("documents")
        .select("url")
        .eq("id", document_id)
        .eq("office_id", office_id)
        .limit(1)
        .execute()
    )
    response = (
        supabase.table("documents").delete().eq("id", document_id).eq("office_id", office_id).execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Document not found")

    key = (existing.data[0]["url"] if existing.data else None)
    if key and not str(key).startswith("http"):
        await gcs.delete_file(key)
    return {"success": True}
