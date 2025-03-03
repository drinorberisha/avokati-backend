-- Create legal_document table
CREATE TABLE IF NOT EXISTS legaldocument (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    document_type TEXT NOT NULL,
    document_metadata JSONB,
    vector_id TEXT,
    is_abolished BOOLEAN DEFAULT FALSE,
    is_updated BOOLEAN DEFAULT FALSE,
    parent_document_id UUID REFERENCES legaldocument(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_legaldocument_title ON legaldocument(title);
CREATE INDEX IF NOT EXISTS idx_legaldocument_document_type ON legaldocument(document_type);
CREATE INDEX IF NOT EXISTS idx_legaldocument_is_abolished ON legaldocument(is_abolished);
CREATE INDEX IF NOT EXISTS idx_legaldocument_is_updated ON legaldocument(is_updated);

-- Create trigger for updated_at
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the trigger if it exists to avoid errors
DROP TRIGGER IF EXISTS update_legaldocument_modtime ON legaldocument;

-- Create the trigger
CREATE TRIGGER update_legaldocument_modtime
BEFORE UPDATE ON legaldocument
FOR EACH ROW
EXECUTE FUNCTION update_modified_column();

-- Set up Row Level Security (RLS)
ALTER TABLE legaldocument ENABLE ROW LEVEL SECURITY;

-- Drop existing policies to avoid errors
DROP POLICY IF EXISTS "Authenticated users can read all legal documents" ON legaldocument;
DROP POLICY IF EXISTS "Admin users can insert, update, and delete legal documents" ON legaldocument;
DROP POLICY IF EXISTS "Legal staff can insert and update legal documents" ON legaldocument;
DROP POLICY IF EXISTS "Legal staff can update legal documents" ON legaldocument;

-- Create policy for authenticated users
CREATE POLICY "Authenticated users can read all legal documents"
ON legaldocument
FOR SELECT
USING (auth.role() = 'authenticated');

-- Create policy for admin users
CREATE POLICY "Admin users can insert, update, and delete legal documents"
ON legaldocument
USING (auth.role() = 'authenticated' AND EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid() AND users.role = 'admin'
));

-- Create policy for legal staff
CREATE POLICY "Legal staff can insert and update legal documents"
ON legaldocument
FOR INSERT
WITH CHECK (auth.role() = 'authenticated' AND EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid() AND users.role IN ('admin', 'attorney', 'paralegal')
));

-- Create policy for legal staff to update documents
CREATE POLICY "Legal staff can update legal documents"
ON legaldocument
FOR UPDATE
USING (auth.role() = 'authenticated' AND EXISTS (
    SELECT 1 FROM users
    WHERE users.id = auth.uid() AND users.role IN ('admin', 'attorney', 'paralegal')
)); 