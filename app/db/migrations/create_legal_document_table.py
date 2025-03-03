"""
Migration script to create the legal_document table.
"""
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, JSON, MetaData, Table
from sqlalchemy.sql import func
import uuid

metadata = MetaData()

# Define the legal_document table
legal_document = Table(
    "legaldocument",
    metadata,
    Column("id", String, primary_key=True, index=True, default=lambda: str(uuid.uuid4())),
    Column("title", String, index=True, nullable=False),
    Column("content", Text, nullable=False),
    Column("document_type", String, index=True, nullable=False),
    Column("metadata", JSON, nullable=True),
    Column("vector_id", String, nullable=True),
    Column("is_abolished", Boolean, default=False),
    Column("is_updated", Boolean, default=False),
    Column("parent_document_id", String, ForeignKey("legaldocument.id"), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), onupdate=func.now())
)

# Migration function to be called by Alembic
def upgrade(connection):
    metadata.create_all(connection)

def downgrade(connection):
    metadata.drop_all(connection) 