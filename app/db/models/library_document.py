from sqlalchemy import Column, Text, DateTime, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class LibraryDocument(Base):
    __tablename__ = "library_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    office_id = Column(UUID(as_uuid=True), nullable=False)
    owner_id = Column(UUID(as_uuid=True), nullable=True)
    title = Column(Text, nullable=False)
    document_type = Column(Text, nullable=True)
    file_name = Column(Text, nullable=True)
    file_url = Column(Text, nullable=False)   # S3 object key
    file_size = Column(BigInteger, nullable=True)
    mime_type = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
