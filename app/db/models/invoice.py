from sqlalchemy import Column, Date, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    case_id = Column(UUID(as_uuid=True), ForeignKey("cases.id"), nullable=True)
    due_date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(Text, nullable=False, server_default="draft")

    client = relationship("Client")
    case = relationship("Case")
