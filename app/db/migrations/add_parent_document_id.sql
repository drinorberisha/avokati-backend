-- Add parent_document_id column to legaldocument table
ALTER TABLE legaldocument
ADD COLUMN parent_document_id UUID REFERENCES legaldocument(id) ON DELETE SET NULL;

-- Add index for better query performance
CREATE INDEX idx_legaldocument_parent_id ON legaldocument(parent_document_id);

COMMENT ON COLUMN legaldocument.parent_document_id IS 'Reference to parent document for hierarchical relationships'; 