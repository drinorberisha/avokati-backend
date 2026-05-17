from sqlalchemy import Column, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base

class Client(Base):
    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    phone = Column(Text, nullable=True)
    address = Column(Text, nullable=True)
    client_since = Column(DateTime(timezone=True), server_default=func.current_timestamp())

    # Relationships
    cases = relationship("Case", back_populates="client")
    documents = relationship("Document", back_populates="client") 
