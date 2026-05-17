from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CaseType(str, Enum):
    civil = "civil"
    criminal = "criminal"
    family = "family"
    corporate = "corporate"
    administrative = "administrative"
    labor = "labor"
    tax = "tax"
    intellectual_property = "intellectual_property"
    real_estate = "real_estate"
    other = "other"


class CaseStatus(str, Enum):
    open = "open"
    pending = "pending"
    closed = "closed"


class CaseBase(BaseModel):
    name: str
    type: CaseType
    client_id: UUID
    status: CaseStatus = CaseStatus.open
    court: Optional[str] = None
    judge: Optional[str] = None
    description: Optional[str] = None


class CaseCreate(CaseBase):
    pass


class CaseUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[CaseType] = None
    client_id: Optional[UUID] = None
    status: Optional[CaseStatus] = None
    court: Optional[str] = None
    judge: Optional[str] = None
    description: Optional[str] = None


class ClientInfo(BaseModel):
    id: UUID
    name: str
    email: str
    phone: Optional[str] = None


class Case(CaseBase):
    id: UUID
    client: Optional[ClientInfo] = None

    class Config:
        from_attributes = True


class CaseResponse(Case):
    pass


class CaseInDB(Case):
    pass
