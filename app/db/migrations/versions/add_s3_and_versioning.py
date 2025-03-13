"""Add S3 fields and versioning support

Revision ID: add_s3_and_versioning
Revises: previous_revision
Create Date: 2024-03-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = 'add_s3_and_versioning'
down_revision = None  # Update this with the previous migration's revision ID
branch_labels = None
depends_on = None


def upgrade():
    # Create legal_document_version table
    op.create_table(
        'legal_document_version',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('document_id', sa.String(), nullable=False),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('file_key', sa.String(), nullable=False),
        sa.Column('file_name', sa.String(), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(), nullable=False),
        sa.Column('changes_description', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['legaldocument.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_legal_document_version_id', 'legal_document_version', ['id'], unique=False)
    op.create_index('ix_legal_document_version_document_id', 'legal_document_version', ['document_id'], unique=False)
    op.create_index('ix_legal_document_version_version_number', 'legal_document_version', ['version_number'], unique=False)

    # Add new columns to legaldocument table
    op.add_column('legaldocument', sa.Column('file_key', sa.String(), nullable=True))
    op.add_column('legaldocument', sa.Column('file_name', sa.String(), nullable=True))
    op.add_column('legaldocument', sa.Column('file_size', sa.Integer(), nullable=True))
    op.add_column('legaldocument', sa.Column('mime_type', sa.String(), nullable=True))
    op.add_column('legaldocument', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('legaldocument', sa.Column('parent_version_id', sa.String(), nullable=True))

    # Add foreign key constraint for parent_version_id
    op.create_foreign_key(
        'fk_legaldocument_parent_version',
        'legaldocument', 'legal_document_version',
        ['parent_version_id'], ['id']
    )

    # Drop old columns
    op.drop_column('legaldocument', 'file_path')
    op.drop_column('legaldocument', 'original_filename')
    op.drop_column('legaldocument', 'parent_document_id')


def downgrade():
    # Add back old columns
    op.add_column('legaldocument', sa.Column('parent_document_id', sa.String(), nullable=True))
    op.add_column('legaldocument', sa.Column('original_filename', sa.String(), nullable=True))
    op.add_column('legaldocument', sa.Column('file_path', sa.String(), nullable=True))

    # Drop new columns from legaldocument
    op.drop_constraint('fk_legaldocument_parent_version', 'legaldocument', type_='foreignkey')
    op.drop_column('legaldocument', 'parent_version_id')
    op.drop_column('legaldocument', 'version')
    op.drop_column('legaldocument', 'mime_type')
    op.drop_column('legaldocument', 'file_size')
    op.drop_column('legaldocument', 'file_name')
    op.drop_column('legaldocument', 'file_key')

    # Drop legal_document_version table and its indexes
    op.drop_index('ix_legal_document_version_version_number', table_name='legal_document_version')
    op.drop_index('ix_legal_document_version_document_id', table_name='legal_document_version')
    op.drop_index('ix_legal_document_version_id', table_name='legal_document_version')
    op.drop_table('legal_document_version') 