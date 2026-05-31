from enum import Enum
from typing import Optional
from pydantic import BaseModel, EmailStr
from datetime import datetime
from uuid import UUID

class UserRole(str, Enum):
    attorney = "attorney"
    paralegal = "paralegal"
    admin = "admin"
    client = "client"

class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = True
    role: Optional[UserRole] = UserRole.paralegal
    full_name: Optional[str] = None
    phone: Optional[str] = None
    bar_number: Optional[str] = None
    is_superuser: bool = False

class UserCreate(UserBase):
    password: str

class UserUpdate(UserBase):
    password: Optional[str] = None

class UserInDB(UserBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    hashed_password: str

class User(UserBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    # Office (multi-tenant) membership. office_id is None until the user onboards.
    office_id: Optional[UUID] = None
    office_role: Optional[str] = "member"

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    email: Optional[str] = None
    permissions: Optional[list[str]] = None
