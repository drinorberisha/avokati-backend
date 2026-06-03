from sqlalchemy import Column, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class Consent(Base):
    """A user's consent decision for a processing purpose (e.g. AI processing).

    One current row per (user_id, purpose); `withdrawn_at` is null while the
    consent is active. A `version` mismatch (vs app/core/consent.py
    AI_CONSENT_VERSION) means the disclosure text changed and re-consent is
    required. See docs/COMPLIANCE_PLAN.md Phase 2.
    """

    __tablename__ = "consents"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id = Column(UUID(as_uuid=True), nullable=False)
    office_id = Column(UUID(as_uuid=True), nullable=False)
    purpose = Column(Text, nullable=False)
    version = Column(Text, nullable=False)
    granted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    withdrawn_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
