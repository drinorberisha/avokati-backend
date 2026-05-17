from sqlalchemy import Column, Date, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class CaseMilestone(Base):
    __tablename__ = "case_milestones"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    due_date = Column(Date, nullable=True)
    status = Column(Text, nullable=False, server_default="not-started")
    priority = Column(Text, nullable=False, server_default="medium")

    case = relationship("Case", back_populates="milestones")
