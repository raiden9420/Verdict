-- Session ownership, embedding provenance, private storage, and durable audit errors.
-- Safe to apply repeatedly. The API remains compatible while this migration is pending,
-- but applying it is required for ownership enforcement on newly uploaded papers.

ALTER TABLE papers ADD COLUMN IF NOT EXISTS session_id TEXT;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS embedding_space TEXT;
ALTER TABLE audits ADD COLUMN IF NOT EXISTS error_message TEXT;

CREATE INDEX IF NOT EXISTS idx_papers_session ON papers(session_id);

-- PDFs are served only after application-level session authorization. Signed
-- URLs expire quickly; the bucket itself must not remain publicly readable.
UPDATE storage.buckets SET public = false WHERE id = 'papers';
DROP POLICY IF EXISTS "Public Access" ON storage.objects;
