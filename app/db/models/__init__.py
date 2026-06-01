from app.db.models.user import User, UserRole
from app.db.models.client import Client
from app.db.models.case import Case, CaseStatus
from app.db.models.case_milestone import CaseMilestone
from app.db.models.document import Document
from app.db.models.event import Event
from app.db.models.invoice import Invoice
from app.db.models.audit import AuditLog
from app.db.models.legal_document import LegalDocument
from app.db.models.chat_session import ChatSession, ChatMessage
from app.db.models.template import Template
from app.db.models.library_document import LibraryDocument

# Export all models and enums
__all__ = [
    'User', 'UserRole',
    'Client',
    'Case', 'CaseStatus',
    'CaseMilestone',
    'Document',
    'Event', 'Invoice',
    'AuditLog',
    'LegalDocument',
    'ChatSession', 'ChatMessage',
    'Template',
    'LibraryDocument',
]

# This ensures all models are imported in the correct order 
