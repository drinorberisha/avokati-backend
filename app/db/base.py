from app.db.base_class import Base
from app.db.models.user import User
from app.db.models.client import Client
from app.db.models.case import Case
from app.db.models.case_milestone import CaseMilestone
from app.db.models.document import Document
from app.db.models.event import Event
from app.db.models.invoice import Invoice
from app.db.models.audit import AuditLog

# All models are imported here for SQLAlchemy to discover them 
