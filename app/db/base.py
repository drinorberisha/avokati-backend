from app.db.base_class import Base
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.case import Case
from app.db.models.document import Document, DocumentVersion, DocumentCollaborator
from app.db.models.audit import AuditLog

# All models are imported here for SQLAlchemy to discover them 