-- Verdict citation-integrity overhaul: persist each paper's extracted bibliography.

BEGIN;

ALTER TABLE public.papers
    ADD COLUMN IF NOT EXISTS reference_list JSONB;

-- Make a partially applied development migration safe to resume.
UPDATE public.papers
SET reference_list = '[]'::jsonb
WHERE reference_list IS NULL
   OR jsonb_typeof(reference_list) <> 'array';

ALTER TABLE public.papers
    ALTER COLUMN reference_list SET DEFAULT '[]'::jsonb,
    ALTER COLUMN reference_list SET NOT NULL;

ALTER TABLE public.papers
    DROP CONSTRAINT IF EXISTS papers_reference_list_array_check,
    ADD CONSTRAINT papers_reference_list_array_check
        CHECK (jsonb_typeof(reference_list) = 'array');

COMMIT;
