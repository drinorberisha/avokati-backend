from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ConsentStatusOut(BaseModel):
    """Current AI-processing consent state for the caller."""
    purpose: str
    consented: bool                  # true only if granted AND version is current
    current_version: str             # the version the app requires right now
    consented_version: Optional[str] = None  # the version the user last accepted (if any)
    granted_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
