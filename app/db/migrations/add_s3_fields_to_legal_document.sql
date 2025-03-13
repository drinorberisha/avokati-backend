-- Add S3 and file-related fields to legal_document table
ALTER TABLE legaldocument
    ADD COLUMN IF NOT EXISTS file_key TEXT,
    ADD COLUMN IF NOT EXISTS file_name TEXT,
    ADD COLUMN IF NOT EXISTS file_size BIGINT,
    ADD COLUMN IF NOT EXISTS mime_type TEXT,
    ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS parent_version_id UUID REFERENCES legaldocument(id);

-- Create legal_document_version table for version tracking
CREATE TABLE IF NOT EXISTS legal_document_version (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID REFERENCES legaldocument(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    file_key TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type TEXT NOT NULL,
    created_by_id UUID NOT NULL,
    changes_description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add indexes for better performance
CREATE INDEX IF NOT EXISTS idx_legal_document_version_document_id ON legal_document_version(document_id);
CREATE INDEX IF NOT EXISTS idx_legal_document_version_created_by ON legal_document_version(created_by_id);

-- Add comment for documentation
COMMENT ON TABLE legal_document_version IS 'Stores version history for legal documents';
COMMENT ON COLUMN legaldocument.file_key IS 'S3 key for the document file';
COMMENT ON COLUMN legaldocument.parent_version_id IS 'Reference to previous version of this document'; 