from app.db.models.user import User, UserRole
from app.db.models.client import Client, ClientStatus
from app.db.models.case import Case, CaseStatus
from app.db.models.document import Document, DocumentStatus
from app.db.models.document_version import DocumentVersion
from app.db.models.document_collaborator import DocumentCollaborator, CollaboratorRole
from app.db.models.audit import AuditLog
from app.db.models.legal_document import LegalDocument

# Export all models and enums
__all__ = [
    'User', 'UserRole',
    'Client', 'ClientStatus',
    'Case', 'CaseStatus',
    'Document', 'DocumentStatus',
    'DocumentVersion',
    'DocumentCollaborator', 'CollaboratorRole',
    'AuditLog',
    'LegalDocument'
]

# This ensures all models are imported in the correct order 