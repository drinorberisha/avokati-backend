from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.db.models.document import Document
from app.db.models.document_version import DocumentVersion
from app.db.models.document_collaborator import DocumentCollaborator
from app.schemas.document import (
    DocumentCreate, 
    DocumentUpdate,
    DocumentVersionCreate,
    DocumentCollaboratorCreate,
    CollaboratorRole
)
from app.core.s3 import s3
from datetime import datetime

def get_document(db: Session, document_id: str) -> Optional[Document]:
    return db.query(Document).filter(Document.id == document_id).first()

def get_documents(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    case_id: Optional[str] = None,
    client_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> List[Document]:
    """
    Get documents with optional filtering by case, client, or user access.
    """
    query = db.query(Document)
    
    if case_id:
        query = query.filter(Document.case_id == case_id)
    if client_id:
        query = query.filter(Document.client_id == client_id)
    if user_id:
        # Get documents where user is a collaborator
        query = query.join(DocumentCollaborator).filter(
            DocumentCollaborator.user_id == user_id
        )
    
    return query.offset(skip).limit(limit).all()

def create_document(db: Session, document_data: Dict[str, Any]) -> Document:
    db_document = Document(**document_data)
    db.add(db_document)
    db.commit()
    db.refresh(db_document)
    
    # Create initial version
    version = DocumentVersion(
        document_id=db_document.id,
        version_number=1,
        file_key=document_data['file_key'],
        file_name=document_data['file_name'],
        file_size=document_data['file_size'],
        mime_type=document_data['mime_type'],
        created_by_id=document_data.get('created_by_id')
    )
    db.add(version)
    db.commit()
    
    return db_document

def update_document(
    db: Session,
    document: Document,
    document_in: DocumentUpdate,
    user_id: str
) -> Document:
    update_data = document_in.model_dump(exclude_unset=True)
    
    # If file-related fields are updated, create a new version
    if any(field in update_data for field in ['file_key', 'file_name', 'file_size', 'mime_type']):
        version = DocumentVersion(
            document_id=document.id,
            version_number=document.version + 1,
            file_key=update_data.get('file_key', document.file_key),
            file_name=update_data.get('file_name', document.file_name),
            file_size=update_data.get('file_size', document.file_size),
            mime_type=update_data.get('mime_type', document.mime_type),
            created_by_id=user_id
        )
        db.add(version)
        update_data['version'] = document.version + 1
    
    for field, value in update_data.items():
        setattr(document, field, value)
    
    document.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(document)
    return document

def delete_document(db: Session, document_id: str) -> Document:
    document = get_document(db, document_id=document_id)
    
    # Delete all versions from S3
    versions = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id
    ).all()
    
    for version in versions:
        s3.delete_file(version.file_key)
        db.delete(version)
    
    # Delete main document file from S3
    if document.file_key:
        s3.delete_file(document.file_key)
    
    db.delete(document)
    db.commit()
    return document

def create_version(
    db: Session,
    document_id: str,
    file_path: str,
    size: str,
    created_by_id: str,
    changes_description: Optional[str] = None
) -> DocumentVersion:
    """
    Create a new version of a document.
    """
    # Get current version number
    current_version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id
    ).count()
    
    db_version = DocumentVersion(
        document_id=document_id,
        version_number=current_version + 1,
        file_path=file_path,
        size=size,
        created_by_id=created_by_id,
        changes_description=changes_description
    )
    db.add(db_version)
    db.commit()
    db.refresh(db_version)
    return db_version

def add_collaborator(
    db: Session,
    document_id: str,
    user_id: str,
    role: str
) -> DocumentCollaborator:
    """
    Add a collaborator to a document.
    """
    db_collaborator = DocumentCollaborator(
        document_id=document_id,
        user_id=user_id,
        role=role
    )
    db.add(db_collaborator)
    db.commit()
    db.refresh(db_collaborator)
    return db_collaborator

def has_access(db: Session, document_id: str, user_id: str) -> bool:
    """
    Check if user has any access to the document.
    """
    return db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id,
        DocumentCollaborator.user_id == user_id
    ).first() is not None

def has_edit_access(db: Session, document_id: str, user_id: str) -> bool:
    """
    Check if user has edit access to the document.
    """
    collaborator = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id,
        DocumentCollaborator.user_id == user_id
    ).first()
    
    return collaborator is not None and collaborator.role in [
        CollaboratorRole.EDITOR,
        CollaboratorRole.OWNER
    ]

def is_owner(db: Session, document_id: str, user_id: str) -> bool:
    """
    Check if user is the owner of the document.
    """
    collaborator = db.query(DocumentCollaborator).filter(
        DocumentCollaborator.document_id == document_id,
        DocumentCollaborator.user_id == user_id,
        DocumentCollaborator.role == CollaboratorRole.OWNER
    ).first()
    
    return collaborator is not None 