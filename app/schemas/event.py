from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class EventType(str, Enum):
    court = "court"
    meeting = "meeting"
    deadline = "deadline"


class EventBase(BaseModel):
    title: str
    type: EventType
    description: Optional[str] = None
    time: Optional[str] = None
    date_time: datetime


class EventCreate(EventBase):
    pass


class EventUpdate(BaseModel):
    title: Optional[str] = None
    type: Optional[EventType] = None
    description: Optional[str] = None
    time: Optional[str] = None
    date_time: Optional[datetime] = None


class Event(EventBase):
    id: UUID

    class Config:
        from_attributes = True
