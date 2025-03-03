from enum import Enum
from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr

class ClientStatus(str, Enum):
    active = "active"
    inactive = "inactive"

class ClientBase(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = None
    status: ClientStatus = ClientStatus.active
    address: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientUpdate(ClientBase):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    status: Optional[ClientStatus] = None

class Client(ClientBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ClientInDB(Client):
    """Database representation of a client, with any additional DB-specific fields."""
    pass 