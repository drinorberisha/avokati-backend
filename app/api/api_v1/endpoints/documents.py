from datetime import datetime
from typing import Any, List, Optional
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.auth import get_current_user
from app.core.s3 import s3
from app.core.supabase import get_supabase_client
from app.schemas.document import Document, DocumentCategory, DocumentUpdate
from app.schemas.user import User

router = APIRouter()


def _normalize_document(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "client_id": row.get("client_id"),
        "case_id": row.get("case_id"),
        "description": row.get("description"),
        "url": row["url"],
        "created_at": row.get("created_at"),
    }


@router.get("/upload-url")
async def get_upload_url(
    file_name: str,
    content_type: str,
    current_user: User = Depends(get_current_user),
) -> Any:
    file_key = s3.generate_file_key(file_name, str(current_user.id))
    upload_url = s3.generate_presigned_url(file_key, "put_object", {"ContentType": content_type})
    return {"uploadUrl": upload_url, "fileKey": file_key}


@router.post("/", response_model=Document, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: UploadFile = File(...),
    data: str = Form(...),
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = json.loads(data)
    case_id = payload.get("case_id")
    client_id = payload.get("client_id")
    if bool(case_id) == bool(client_id):
        raise HTTPException(status_code=400, detail="Document must be associated with exactly one client or one case")

    file_key = s3.generate_file_key(file.filename, str(current_user.id))
    uploaded = await s3.upload_file(file.file, file_key, content_type=file.content_type)
    if not uploaded:
        raise HTTPException(status_code=500, detail="Failed to upload file")

    url = await s3.generate_presigned_url(file_key, "get_object", expiration=3600)
    document = {
        "name": payload["name"],
        "category": payload["category"],
        "client_id": client_id,
        "case_id": case_id,
        "description": payload.get("description"),
        "url": url,
        "created_at": datetime.utcnow().isoformat(),
    }
    response = supabase.table("documents").insert(document).execute()
    if not response.data:
        await s3.delete_file(file_key)
        raise HTTPException(status_code=400, detail="Failed to create document")
    return _normalize_document(response.data[0])


@router.get("/", response_model=List[Document])
async def get_documents(
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("documents").select("id, name, category, client_id, case_id, description, url, created_at").execute()
    return [_normalize_document(row) for row in response.data or []]


@router.get("/{document_id}", response_model=Document)
async def read_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("documents").select("id, name, category, client_id, case_id, description, url, created_at").eq("id", document_id).single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return _normalize_document(response.data)


@router.put("/{document_id}", response_model=Document)
async def update_document(
    document_id: str,
    file: Optional[UploadFile] = File(None),
    data: str = Form(...),
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    payload = json.loads(data)
    update_data = DocumentUpdate(**payload).model_dump(mode="json", exclude_unset=True)

    if "category" in update_data:
        update_data["category"] = DocumentCategory(update_data["category"]).value
    if file:
        file_key = s3.generate_file_key(file.filename, str(current_user.id))
        uploaded = await s3.upload_file(file.file, file_key, content_type=file.content_type)
        if not uploaded:
            raise HTTPException(status_code=500, detail="Failed to upload file")
        update_data["url"] = await s3.generate_presigned_url(file_key, "get_object", expiration=3600)

    response = supabase.table("documents").update(update_data).eq("id", document_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return _normalize_document(response.data[0])


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    supabase=Depends(get_supabase_client),
) -> Any:
    response = supabase.table("documents").delete().eq("id", document_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True}
