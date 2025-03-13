from typing import List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query, Form
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.crud import document as document_crud
from app.schemas.document import (
    Document, 
    DocumentCreate, 
    DocumentUpdate, 
    DocumentResponse,
    DocumentVersion,
    DocumentVersionResponse
)
from app.core.auth import get_current_user
from app.schemas.user import User, UserRole
from app.core.storage import upload_file, delete_file
from app.core.s3 import s3
from app.core.supabase import supabase
import uuid
from app.db.models.user import User as DBUser
import json
from datetime import datetime
from app.core.constants import S3_BUCKET_NAME
import asyncio

router = APIRouter()

@router.get("/upload-url")
async def get_upload_url(
    file_name: str,
    content_type: str,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get a pre-signed URL for uploading a file to S3.
    """
    try:
        file_key = s3.generate_file_key(file_name, str(current_user.id))
        upload_url = s3.generate_presigned_url(file_key, 'put_object', {
            'ContentType': content_type
        })
        return {
            "uploadUrl": upload_url,
            "fileKey": file_key
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/")
async def create_document(
    file: UploadFile = File(...),
    data: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    try:
        # Parse the JSON data
        document_data = json.loads(data)
        
        # Validate that either case_id or client_id is provided, but not both
        case_id = document_data.get("case_id")
        client_id = document_data.get("client_id")
        
        if not case_id and not client_id:
            raise HTTPException(
                status_code=400,
                detail="Either case_id or client_id must be provided"
            )
            
        if case_id and client_id:
            raise HTTPException(
                status_code=400,
                detail="Document cannot be associated with both a case and a client"
            )
        
        # Generate unique file key
        file_key = s3.generate_file_key(file.filename, str(current_user.id))
        
        # Upload file to S3
        upload_success = await s3.upload_file(
            file.file,
            file_key,
            content_type=file.content_type
        )
        
        if not upload_success:
            raise HTTPException(
                status_code=500,
                detail="Failed to upload file to S3"
            )
        
        # Generate presigned URL
        download_url = await s3.generate_presigned_url(
            file_key,
            'get_object',
            expiration=3600
        )
        
        if not download_url:
            # Clean up S3 if URL generation fails
            await s3.delete_file(file_key)
            raise HTTPException(
                status_code=500,
                detail="Failed to generate download URL"
            )
        
        # Create document record in Supabase
        document = {
            "title": document_data["title"],
            "type": file.content_type,
            "category": document_data["category"],
            "status": "draft",
            "size": str(file.size),
            "version": 1,
            "file_path": file_key,
            "file_name": file.filename,
            "file_size": file.size,
            "mime_type": file.content_type,
            "download_url": download_url,
            "tags": document_data.get("tags", []),
            "case_id": str(case_id) if case_id else None,
            "client_id": str(client_id) if client_id else None,
            "created_by": str(current_user.id),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": {
                "author": current_user.full_name,
                "createdAt": datetime.utcnow().isoformat(),
                "lastModifiedBy": current_user.full_name,
                "versionHistory": [{
                    "version": 1,
                    "modifiedAt": datetime.utcnow().isoformat(),
                    "modifiedBy": current_user.full_name,
                    "changes": "Initial document creation"
                }]
            },
            "collaborators": [{
                "id": str(current_user.id),
                "name": current_user.full_name,
                "email": current_user.email,
                "role": "owner",
                "addedAt": datetime.utcnow().isoformat()
            }]
        }
        
        # Insert document into Supabase
        response = supabase.table('documents').insert(document).execute()
        
        if not response.data:
            # Clean up S3 if Supabase insert fails
            await s3.delete_file(file_key)
            raise HTTPException(
                status_code=400,
                detail="Failed to create document in database"
            )
            
        return response.data[0]
        
    except Exception as e:
        print(f"Error creating document: {str(e)}")  # Add logging
        # Clean up S3 file if it was uploaded
        if 'file_key' in locals():
            await s3.delete_file(file_key)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create document: {str(e)}"
        )

@router.get("/")
async def get_documents(
    current_user: User = Depends(get_current_user)
):
    try:
        # Execute Supabase query without await
        response = supabase.table('documents')\
            .select('*')\
            .execute()
            
        # The response is already a dict with 'data' key in newer versions
        if not response.data:
            return []
            
        # Update download URLs for all documents
        documents = response.data
        for doc in documents:
            if doc.get('file_path'):
                # Generate presigned URL
                doc['download_url'] = await s3.generate_presigned_url(
                    doc['file_path'],
                    'get_object',
                    expiration=3600
                )
                
        return documents
        
    except Exception as e:
        print(f"Error fetching documents: {str(e)}")  # Add logging
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch documents: {str(e)}"
        )

@router.get("/{document_id}")
async def read_document(
    document_id: str,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get document by ID.
    """
    try:
        # Get document from Supabase
        response = supabase.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
            
        if response.error:
            raise HTTPException(
                status_code=404,
                detail="Document not found"
            )
            
        document = response.data
        
        # Check if user has access (is collaborator)
        if not any(collab['id'] == current_user.id for collab in document['collaborators']):
            raise HTTPException(
                status_code=403,
                detail="Not enough permissions"
            )
        
        # Update download URL
        if document['file_path']:
            document['download_url'] = s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': S3_BUCKET_NAME,
                    'Key': document['file_path']
                },
                ExpiresIn=3600
            )
        
        return document
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch document: {str(e)}"
        )

@router.put("/{document_id}")
async def update_document(
    document_id: str,
    file: Optional[UploadFile] = File(None),
    data: str = Form(...),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Update document and optionally create new version.
    """
    try:
        # Parse update data
        update_data = json.loads(data)
        
        # Get existing document
        response = supabase.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
            
        if response.error:
            raise HTTPException(
                status_code=404,
                detail="Document not found"
            )
            
        document = response.data
        
        # Check if user has edit access
        has_edit_access = any(
            collab['id'] == current_user.id and collab['role'] in ['owner', 'editor']
            for collab in document['collaborators']
        )
        if not has_edit_access:
            raise HTTPException(
                status_code=403,
                detail="Not enough permissions"
            )
        
        # Handle file update if provided
        if file:
            # Generate new file key
            file_key = f"documents/{current_user.id}/{uuid.uuid4()}-{file.filename}"
            
            # Upload new file to S3
            await s3.upload_file(
                file.file,
                file_key,
                content_type=file.content_type
            )
            
            # Generate new download URL
            download_url = s3.generate_presigned_url(
                file_key,
                'get_object',
                expiration=3600
            )
            
            # Delete old file if exists
            if document['file_path']:
                await s3.delete_file(document['file_path'])
            
            # Update file-related fields
            update_data.update({
                "file_path": file_key,
                "file_name": file.filename,
                "file_size": file.size,
                "mime_type": file.content_type,
                "download_url": download_url,
            })
        
        # Update version and metadata
        current_version = document['version']
        update_data.update({
            "version": current_version + 1,
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": {
                **document['metadata'],
                "lastModifiedBy": current_user.full_name,
                "versionHistory": [
                    {
                        "version": current_version + 1,
                        "modifiedAt": datetime.utcnow().isoformat(),
                        "modifiedBy": current_user.full_name,
                        "changes": update_data.get("changes_description", "Document updated")
                    },
                    *document['metadata']['versionHistory']
                ]
            }
        })
        
        # Update document in Supabase
        update_response = supabase.table('documents')\
            .update(update_data)\
            .eq('id', document_id)\
            .execute()
            
        if update_response.error:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to update document: {update_response.error.message}"
            )
        
        return update_response.data[0]
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update document: {str(e)}"
        )

@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Delete document and its file from S3.
    Only document owner or admin can delete.
    """
    try:
        # Get document from Supabase
        response = supabase.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
            
        if response.error:
            raise HTTPException(
                status_code=404,
                detail="Document not found"
            )
            
        document = response.data
        
        # Check if user is owner or admin
        is_owner = any(
            collab['id'] == current_user.id and collab['role'] == 'owner' 
            for collab in document['collaborators']
        )
        if not is_owner and current_user.role != 'admin':
            raise HTTPException(
                status_code=403,
                detail="Only document owner or admin can delete documents"
            )
        
        # Delete file from S3
        if document['file_path']:
            await s3.delete_file(document['file_path'])
        
        # Delete document from Supabase
        delete_response = supabase.table('documents')\
            .delete()\
            .eq('id', document_id)\
            .execute()
            
        if delete_response.error:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to delete document: {delete_response.error.message}"
            )
        
        return {"message": "Document deleted successfully"}
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete document: {str(e)}"
        )

@router.post("/{document_id}/versions")
async def create_version(
    document_id: str,
    file: UploadFile = File(...),
    data: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    try:
        # Parse the JSON data
        version_data = json.loads(data)
        
        # Get existing document
        doc_response = supabase.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .single()\
            .execute()
            
        if doc_response.error:
            raise HTTPException(
                status_code=404,
                detail="Document not found"
            )
            
        document = doc_response.data
        
        # Check if user has edit access
        has_edit_access = any(
            collab['id'] == current_user.id and collab['role'] in ['owner', 'editor']
            for collab in document['collaborators']
        )
        if not has_edit_access:
            raise HTTPException(
                status_code=403,
                detail="Not enough permissions"
            )
        
        # Generate unique file key for the new version
        file_key = f"documents/{current_user.id}/versions/{document_id}/{uuid.uuid4()}-{file.filename}"
        
        # Upload new version to S3
        await s3.upload_file(
            file.file,
            file_key,
            content_type=file.content_type
        )
        
        # Generate download URL for the new version
        download_url = s3.generate_presigned_url(
            file_key,
            'get_object',
            expiration=3600
        )
        
        # Create version record
        new_version = {
            "document_id": document_id,
            "version_number": document['version'] + 1,
            "file_path": file_key,
            "file_name": file.filename,
            "file_size": file.size,
            "mime_type": file.content_type,
            "download_url": download_url,
            "changes_description": version_data.get('changes_description', ''),
            "created_by_id": current_user.id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        version_response = supabase.table('document_versions').insert(new_version).execute()
        
        if version_response.error:
            # Clean up S3 if version creation fails
            await s3.delete_file(file_key)
            raise HTTPException(
                status_code=400,
                detail=f"Failed to create version: {version_response.error.message}"
            )
        
        # Update document with new version number
        update_data = {
            "version": document['version'] + 1,
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": {
                **document['metadata'],
                "lastModifiedBy": current_user.full_name,
                "versionHistory": [
                    {
                        "version": document['version'] + 1,
                        "modifiedAt": datetime.utcnow().isoformat(),
                        "modifiedBy": current_user.full_name,
                        "changes": version_data.get('changes_description', '')
                    },
                    *document['metadata']['versionHistory']
                ]
            }
        }
        
        doc_update_response = supabase.table('documents')\
            .update(update_data)\
            .eq('id', document_id)\
            .execute()
            
        if doc_update_response.error:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to update document: {doc_update_response.error.message}"
            )
            
        return {
            "document": doc_update_response.data[0],
            "version": version_response.data[0]
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create version: {str(e)}"
        ) 