from sqlalchemy import Column, Text, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from enum import Enum
from app.core.database import Base

class ClientStatus(str, Enum):
    active = "active"
    inactive = "inactive"

class Client(Base):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    phone = Column(Text, nullable=True)
    status = Column(SQLEnum(ClientStatus), nullable=False, server_default=ClientStatus.active.value)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())

    # Relationships
    cases = relationship("Case", back_populates="client")
    documents = relationship("Document", back_populates="client") 