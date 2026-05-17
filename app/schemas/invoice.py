from datetime import date
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class InvoiceStatus(str, Enum):
    draft = "draft"
    sent = "sent"
    paid = "paid"
    overdue = "overdue"


class InvoiceBase(BaseModel):
    client_id: UUID
    case_id: Optional[UUID] = None
    due_date: date
    description: str
    price: float
    status: InvoiceStatus = InvoiceStatus.draft


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    client_id: Optional[UUID] = None
    case_id: Optional[UUID] = None
    due_date: Optional[date] = None
    description: Optional[str] = None
    price: Optional[float] = None
    status: Optional[InvoiceStatus] = None


class ClientInfo(BaseModel):
    id: UUID
    name: str


class CaseInfo(BaseModel):
    id: UUID
    name: str


class Invoice(InvoiceBase):
    id: UUID
    client: Optional[ClientInfo] = None
    case: Optional[CaseInfo] = None

    class Config:
        from_attributes = True
