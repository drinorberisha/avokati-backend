from app.schemas.user import User, UserCreate, UserUpdate, UserInDB
from app.schemas.client import Client, ClientCreate, ClientUpdate, ClientInDB
from app.schemas.case import Case, CaseCreate, CaseUpdate, CaseInDB
from app.schemas.document import Document, DocumentCreate, DocumentUpdate, DocumentInDB
from app.schemas.auth import Token, TokenPayload
from app.schemas.audit import AuditLog, AuditLogCreate
from app.schemas.legal_document import (
    LegalDocumentBase, LegalDocumentCreate, LegalDocumentUpdate, 
    LegalDocumentInDB, LegalDocumentResponse, LegalDocumentSearchQuery,
    LegalDocumentSearchResult, LegalDocumentBatchCreate
)

# Export all schemas
__all__ = [
    'User', 'UserCreate', 'UserUpdate', 'UserInDB',
    'Client', 'ClientCreate', 'ClientUpdate', 'ClientInDB',
    'Case', 'CaseCreate', 'CaseUpdate', 'CaseInDB',
    'Document', 'DocumentCreate', 'DocumentUpdate', 'DocumentInDB',
    'Token', 'TokenPayload',
    'AuditLog', 'AuditLogCreate',
    'LegalDocumentBase', 'LegalDocumentCreate', 'LegalDocumentUpdate',
    'LegalDocumentInDB', 'LegalDocumentResponse', 'LegalDocumentSearchQuery',
    'LegalDocumentSearchResult', 'LegalDocumentBatchCreate'
] 