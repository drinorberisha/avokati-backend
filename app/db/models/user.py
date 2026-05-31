from sqlalchemy import Column, String, Boolean, Enum as SQLEnum, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, ENUM
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from enum import Enum

class UserRole(str, Enum):
    attorney = "attorney"
    paralegal = "paralegal"
    admin = "admin"
    client = "client"

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email = Column(Text, unique=True, nullable=False)
    full_name = Column(Text, nullable=True)
    hashed_password = Column(Text, nullable=False)
    is_active = Column(Boolean, server_default='true', nullable=True)
    is_superuser = Column(Boolean, server_default='false', nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at = Column(DateTime(timezone=True), server_default=func.current_timestamp())
    role = Column(ENUM('attorney', 'paralegal', 'admin', 'client', name='user_role', create_type=False), nullable=False, server_default='paralegal')
    phone = Column(Text, nullable=True)
    bar_number = Column(Text, nullable=True)
    # Office (multi-tenant) membership. Nullable until the user onboards into an
    # office. office_role is the office-permission level (owner|admin|member),
    # orthogonal to the professional `role` enum above. See docs/BUILD_ORDER.md.
    office_id = Column(UUID(as_uuid=True), nullable=True)
    office_role = Column(Text, nullable=False, server_default='member')

    # Relationships
    audit_logs = relationship("AuditLog", back_populates="user")
