from enum import Enum
from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, validator

class CaseStatus(str, Enum):
    open = "open"
    pending = "pending"
    closed = "closed"

class CaseBase(BaseModel):
    case_number: str
    title: str
    type: str
    status: CaseStatus = CaseStatus.open
    court: str
    judge: str
    next_hearing: Optional[datetime] = None
    client_id: UUID
    primary_attorney_id: UUID

    class Config:
        from_attributes = True

    @validator('status', pre=True)
    def validate_status(cls, v):
        if isinstance(v, CaseStatus):
            return v
            
        if isinstance(v, str):
            try:
                v_lower = v.lower()
                return CaseStatus(v_lower)
            except ValueError:
                raise ValueError(f"Invalid status value: {v}. Valid values are: {[e.value for e in CaseStatus]}")
        
        raise ValueError(f"Status must be a string or CaseStatus enum, got {type(v)}")

class CaseCreate(CaseBase):
    pass

class CaseUpdate(CaseBase):
    case_number: Optional[str] = None
    title: Optional[str] = None
    type: Optional[str] = None
    status: Optional[CaseStatus] = None
    court: Optional[str] = None
    judge: Optional[str] = None
    client_id: Optional[UUID] = None
    primary_attorney_id: Optional[UUID] = None

class Case(CaseBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class CaseInDB(Case):
    """Database representation of a case, with any additional DB-specific fields."""
    pass

class ClientInfo(BaseModel):
    id: UUID
    name: str
    email: str
    phone: Optional[str] = None
    status: str

    class Config:
        from_attributes = True

class CaseResponse(Case):
    client: Optional[ClientInfo] = None
    
    class Config:
        from_attributes = True 