from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel

# Mirrors the frontend src/components/templates/types.ts model.

VariableType = Literal["text", "number", "date", "select", "boolean"]
TemplateStatus = Literal["draft", "published", "archived"]


class TemplateVariable(BaseModel):
    id: str
    name: str
    type: VariableType = "text"
    required: bool = True
    defaultValue: Optional[str] = None
    options: Optional[List[str]] = None
    description: Optional[str] = None


class TemplateBase(BaseModel):
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    status: TemplateStatus = "draft"
    content: str = ""
    variables: List[TemplateVariable] = []


class TemplateCreate(TemplateBase):
    pass


class TemplateUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    language: Optional[str] = None
    status: Optional[TemplateStatus] = None
    content: Optional[str] = None
    variables: Optional[List[TemplateVariable]] = None


class TemplateOut(TemplateBase):
    id: UUID
    source_type: str = "manual"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
