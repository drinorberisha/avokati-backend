from sqlalchemy import Column, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    title = Column(Text, nullable=False)
    type = Column(Text, nullable=False, server_default="meeting")
    description = Column(Text, nullable=True)
    time = Column(Text, nullable=True)
    date_time = Column(DateTime(timezone=True), nullable=False)
