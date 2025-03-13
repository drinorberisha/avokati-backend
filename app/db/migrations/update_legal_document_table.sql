-- Migration script to update the legal_document table with new columns

-- Note: The 'id' column is already defined as UUID type in the original table creation script.
-- Make sure your application code is using UUID type for the 'id' field, not String.

-- Add status column
ALTER TABLE legaldocument ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'pending' NOT NULL;

-- Add is_annex column
ALTER TABLE legaldocument ADD COLUMN IF NOT EXISTS is_annex BOOLEAN DEFAULT FALSE;

-- Add user_id column
ALTER TABLE legaldocument ADD COLUMN IF NOT EXISTS user_id VARCHAR;

-- Add file_path column
ALTER TABLE legaldocument ADD COLUMN IF NOT EXISTS file_path VARCHAR;

-- Add original_filename column
ALTER TABLE legaldocument ADD COLUMN IF NOT EXISTS original_filename VARCHAR;

-- Make content column nullable
ALTER TABLE legaldocument ALTER COLUMN content DROP NOT NULL;

-- Create index on status column
CREATE INDEX IF NOT EXISTS ix_legaldocument_status ON legaldocument (status);

-- Comment explaining the purpose of each column
COMMENT ON COLUMN legaldocument.status IS 'Status of document processing (pending, processing, processed, failed)';
COMMENT ON COLUMN legaldocument.is_annex IS 'Whether this is an annex law (addition to another law)';
COMMENT ON COLUMN legaldocument.user_id IS 'ID of the user who uploaded the document';
COMMENT ON COLUMN legaldocument.file_path IS 'Path to the uploaded file';
COMMENT ON COLUMN legaldocument.original_filename IS 'Original filename of the uploaded file';