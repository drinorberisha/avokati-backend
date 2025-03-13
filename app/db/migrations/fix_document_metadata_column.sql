-- Migration to fix document_metadata column issue
DO $$
BEGIN
    -- If metadata column exists and document_metadata doesn't
    IF EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'legaldocument' 
        AND column_name = 'metadata'
    ) AND NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'legaldocument' 
        AND column_name = 'document_metadata'
    ) THEN
        -- Rename metadata to document_metadata
        ALTER TABLE legaldocument RENAME COLUMN metadata TO document_metadata;
        
    -- If neither column exists
    ELSIF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'legaldocument' 
        AND column_name IN ('metadata', 'document_metadata')
    ) THEN
        -- Add document_metadata column
        ALTER TABLE legaldocument ADD COLUMN document_metadata JSONB;
    END IF;
    
    -- Ensure the column is of type JSONB
    IF EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'legaldocument' 
        AND column_name = 'document_metadata' 
        AND data_type != 'jsonb'
    ) THEN
        -- Alter column type to JSONB
        ALTER TABLE legaldocument 
        ALTER COLUMN document_metadata TYPE JSONB USING document_metadata::JSONB;
    END IF;
END $$;

-- Add comment for documentation
COMMENT ON COLUMN legaldocument.document_metadata IS 'Store additional metadata like publication date, source, etc.';

-- Create index for better performance on JSON queries if needed
CREATE INDEX IF NOT EXISTS idx_legaldocument_metadata ON legaldocument USING gin (document_metadata); 