from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class OfficeCreate(BaseModel):
    name: str


class OfficeUpdate(BaseModel):
    name: Optional[str] = None


class OfficeOut(BaseModel):
    id: UUID
    name: str
    owner_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    # The requesting user's office_role within this office (owner|admin|member).
    role: Optional[str] = None


class OfficeMemberOut(BaseModel):
    id: UUID
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None          # professional role (attorney|paralegal|admin|client)
    office_role: str = "member"          # office permission (owner|admin|member)
    is_active: Optional[bool] = True


class MemberUpdate(BaseModel):
    role: Optional[str] = None            # professional role: attorney|paralegal|admin|client
    office_role: Optional[str] = None     # admin | member (owner cannot be set via API)
    is_active: Optional[bool] = None


class InviteCreate(BaseModel):
    email: Optional[EmailStr] = None
    role: str = "member"                 # admin | member


class InviteOut(BaseModel):
    id: UUID
    office_id: UUID
    email: Optional[str] = None
    token: str
    role: str
    invited_by: Optional[UUID] = None
    expires_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class InvitePreview(BaseModel):
    office_id: UUID
    office_name: str
    role: str
    email: Optional[str] = None
    expired: bool = False
    accepted: bool = False
