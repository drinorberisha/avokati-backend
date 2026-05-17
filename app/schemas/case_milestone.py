from datetime import date
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class MilestoneStatus(str, Enum):
    not_started = "not-started"
    in_progress = "in-progress"
    completed = "completed"
    overdue = "overdue"


class MilestonePriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class CaseMilestoneBase(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[date] = None
    status: MilestoneStatus = MilestoneStatus.not_started
    priority: MilestonePriority = MilestonePriority.medium


class CaseMilestoneCreate(CaseMilestoneBase):
    pass


class CaseMilestoneUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[date] = None
    status: Optional[MilestoneStatus] = None
    priority: Optional[MilestonePriority] = None


class CaseMilestone(CaseMilestoneBase):
    id: UUID
    case_id: UUID

    class Config:
        from_attributes = True
