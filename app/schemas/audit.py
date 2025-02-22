from enum import Enum
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

class ActionType(str, Enum):
    create = "create"
    update = "update"
    delete = "delete"
    view = "view"
    download = "download"
    login = "login"
    logout = "logout"

class EntityType(str, Enum):
    user = "user"
    client = "client"
    case = "case"
    document = "document"
    template = "template"
    invoice = "invoice"
    timeentry = "timeentry"

class AuditLogBase(BaseModel):
    user_id: UUID
    action: ActionType
    entity_type: EntityType
    entity_id: UUID
    changes: Dict[str, Any] = {}
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    description: Optional[str] = None

class AuditLogCreate(AuditLogBase):
    pass

class AuditLogUpdate(AuditLogBase):
    user_id: Optional[UUID] = None
    action: Optional[ActionType] = None
    entity_type: Optional[EntityType] = None
    entity_id: Optional[UUID] = None
    changes: Optional[Dict[str, Any]] = None

class AuditLog(AuditLogBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True 