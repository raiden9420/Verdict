-- ============================================================
-- Verdict Phase 3 — accounts, product schema, and enforced RLS
--
-- Pre-launch migration: anonymous Phase 1/2 rows are deliberately discarded.
-- The product spec does not require a claim/migration flow for test data.
-- Run as the database owner in the Supabase SQL editor (or via migrations).
-- ============================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Product ownership and configuration columns
-- ---------------------------------------------------------------------------
ALTER TABLE public.papers
    ADD COLUMN IF NOT EXISTS user_id UUID,
    ADD COLUMN IF NOT EXISTS parent_paper_id UUID,
    ADD COLUMN IF NOT EXISTS version_number INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS detected_domain TEXT DEFAULT 'other';

ALTER TABLE public.audits
    ADD COLUMN IF NOT EXISTS user_id UUID,
    ADD COLUMN IF NOT EXISTS mode TEXT DEFAULT 'author',
    ADD COLUMN IF NOT EXISTS domain TEXT DEFAULT 'other';

-- There is intentionally no ownership guess for legacy anonymous data. Delete
-- audit descendants first (their existing ON DELETE CASCADE chain removes
-- rounds, turns, verdicts, and debrief cards), then delete orphan papers/chunks.
DELETE FROM public.audits AS audit
WHERE audit.user_id IS NULL
   OR NOT EXISTS (
       SELECT 1
       FROM public.papers AS paper
       WHERE paper.id = audit.paper_id
         AND paper.user_id = audit.user_id
   );

DELETE FROM public.papers WHERE user_id IS NULL;

-- Normalize safe defaults before adding constraints. This also makes a partially
-- applied development migration recoverable without weakening the final schema.
UPDATE public.papers
SET version_number = 1
WHERE version_number IS NULL OR version_number < 1;

UPDATE public.papers
SET detected_domain = 'other'
WHERE detected_domain IS NULL
   OR detected_domain NOT IN ('ml_cs', 'life_sciences', 'social_science', 'other');

UPDATE public.papers AS child
SET parent_paper_id = NULL
WHERE child.parent_paper_id = child.id
   OR NOT EXISTS (
       SELECT 1
       FROM public.papers AS parent
       WHERE parent.id = child.parent_paper_id
         AND parent.user_id = child.user_id
   );

UPDATE public.audits
SET mode = 'author'
WHERE mode IS NULL OR mode NOT IN ('author', 'reviewer_assist');

UPDATE public.audits
SET domain = 'other'
WHERE domain IS NULL
   OR domain NOT IN ('ml_cs', 'life_sciences', 'social_science', 'other');

UPDATE public.audits
SET strictness_level = 'standard'
WHERE strictness_level IS NULL
   OR strictness_level NOT IN ('constructive', 'standard', 'brutal');

UPDATE public.audits
SET depth = 'fast'
WHERE depth IS NULL OR depth NOT IN ('fast', 'deep', 'exhaustive');

ALTER TABLE public.papers
    ALTER COLUMN user_id SET NOT NULL,
    ALTER COLUMN version_number SET DEFAULT 1,
    ALTER COLUMN version_number SET NOT NULL,
    ALTER COLUMN detected_domain SET DEFAULT 'other',
    ALTER COLUMN detected_domain SET NOT NULL;

ALTER TABLE public.audits
    ALTER COLUMN user_id SET NOT NULL,
    ALTER COLUMN strictness_level SET DEFAULT 'standard',
    ALTER COLUMN strictness_level SET NOT NULL,
    ALTER COLUMN depth SET DEFAULT 'fast',
    ALTER COLUMN depth SET NOT NULL,
    ALTER COLUMN mode SET DEFAULT 'author',
    ALTER COLUMN mode SET NOT NULL,
    ALTER COLUMN domain SET DEFAULT 'other',
    ALTER COLUMN domain SET NOT NULL;

-- Replace anonymous browser ownership completely. Dropping the columns also
-- drops their legacy indexes; explicit DROP INDEX keeps the intent obvious.
DROP INDEX IF EXISTS public.idx_papers_session;
DROP INDEX IF EXISTS public.idx_audits_session;
ALTER TABLE public.papers DROP COLUMN IF EXISTS session_id;
ALTER TABLE public.audits DROP COLUMN IF EXISTS session_id;

-- ---------------------------------------------------------------------------
-- Foreign keys and checks
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'papers_user_id_fkey'
          AND conrelid = 'public.papers'::regclass
    ) THEN
        ALTER TABLE public.papers
            ADD CONSTRAINT papers_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'audits_user_id_fkey'
          AND conrelid = 'public.audits'::regclass
    ) THEN
        ALTER TABLE public.audits
            ADD CONSTRAINT audits_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
    END IF;
END;
$$;

-- Composite keys make it impossible to attach an audit or revision to a paper
-- owned by another account, even if a UUID is guessed and a privileged client
-- accidentally attempts the write.
CREATE UNIQUE INDEX IF NOT EXISTS uq_papers_id_user_id
    ON public.papers(id, user_id);

ALTER TABLE public.papers
    DROP CONSTRAINT IF EXISTS papers_parent_paper_id_fkey,
    DROP CONSTRAINT IF EXISTS papers_parent_owner_fkey,
    ADD CONSTRAINT papers_parent_owner_fkey
        FOREIGN KEY (parent_paper_id, user_id)
        REFERENCES public.papers(id, user_id)
        ON DELETE CASCADE;

ALTER TABLE public.audits
    DROP CONSTRAINT IF EXISTS audits_paper_id_fkey,
    DROP CONSTRAINT IF EXISTS audits_paper_owner_fkey,
    ADD CONSTRAINT audits_paper_owner_fkey
        FOREIGN KEY (paper_id, user_id)
        REFERENCES public.papers(id, user_id)
        ON DELETE CASCADE;

ALTER TABLE public.papers
    DROP CONSTRAINT IF EXISTS papers_version_number_check,
    DROP CONSTRAINT IF EXISTS papers_parent_not_self_check,
    DROP CONSTRAINT IF EXISTS papers_detected_domain_check,
    ADD CONSTRAINT papers_version_number_check
        CHECK (version_number >= 1),
    ADD CONSTRAINT papers_parent_not_self_check
        CHECK (parent_paper_id IS NULL OR parent_paper_id <> id),
    ADD CONSTRAINT papers_detected_domain_check
        CHECK (detected_domain IN ('ml_cs', 'life_sciences', 'social_science', 'other'));

ALTER TABLE public.audits
    DROP CONSTRAINT IF EXISTS audits_strictness_level_check,
    DROP CONSTRAINT IF EXISTS audits_depth_check,
    DROP CONSTRAINT IF EXISTS audits_mode_check,
    DROP CONSTRAINT IF EXISTS audits_domain_check,
    ADD CONSTRAINT audits_strictness_level_check
        CHECK (strictness_level IN ('constructive', 'standard', 'brutal')),
    ADD CONSTRAINT audits_depth_check
        CHECK (depth IN ('fast', 'deep', 'exhaustive')),
    ADD CONSTRAINT audits_mode_check
        CHECK (mode IN ('author', 'reviewer_assist')),
    ADD CONSTRAINT audits_domain_check
        CHECK (domain IN ('ml_cs', 'life_sciences', 'social_science', 'other'));

-- ---------------------------------------------------------------------------
-- Paper-level aggregate reports and revision comparisons
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.final_reports (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_id    UUID NOT NULL UNIQUE
                REFERENCES public.audits(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL DEFAULT 'author',
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT final_reports_mode_check
        CHECK (mode IN ('author', 'reviewer_assist'))
);

CREATE TABLE IF NOT EXISTS public.version_diffs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_id_old  UUID NOT NULL
                  REFERENCES public.audits(id) ON DELETE CASCADE,
    audit_id_new  UUID NOT NULL
                  REFERENCES public.audits(id) ON DELETE CASCADE,
    round_topic   TEXT NOT NULL,
    diff_summary  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT version_diffs_distinct_audits_check
        CHECK (audit_id_old <> audit_id_new),
    CONSTRAINT version_diffs_round_topic_check
        CHECK (round_topic IN (
            'novelty_scope',
            'theoretical_soundness',
            'experimental_setup',
            'reproducibility',
            'limitations_impact',
            'statistical_rigor'
        )),
    CONSTRAINT version_diffs_comparison_unique
        UNIQUE (audit_id_old, audit_id_new, round_topic)
);

-- ---------------------------------------------------------------------------
-- Query and policy-supporting indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_papers_user_uploaded
    ON public.papers(user_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_papers_parent_version
    ON public.papers(parent_paper_id, version_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_papers_parent_version
    ON public.papers(parent_paper_id, version_number)
    WHERE parent_paper_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audits_user_created
    ON public.audits(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audits_paper
    ON public.audits(paper_id);
CREATE INDEX IF NOT EXISTS idx_rounds_audit
    ON public.rounds(audit_id, round_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rounds_audit_number
    ON public.rounds(audit_id, round_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rounds_audit_topic
    ON public.rounds(audit_id, topic);
CREATE UNIQUE INDEX IF NOT EXISTS uq_turns_round_sequence
    ON public.turns(round_id, sequence);
CREATE UNIQUE INDEX IF NOT EXISTS uq_verdicts_round_exchange
    ON public.verdicts(round_id, exchange_number);
CREATE INDEX IF NOT EXISTS idx_debrief_cards_round
    ON public.debrief_cards(round_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_debrief_cards_round
    ON public.debrief_cards(round_id);
CREATE INDEX IF NOT EXISTS idx_final_reports_audit
    ON public.final_reports(audit_id);
CREATE INDEX IF NOT EXISTS idx_version_diffs_old_audit
    ON public.version_diffs(audit_id_old);
CREATE INDEX IF NOT EXISTS idx_version_diffs_new_audit
    ON public.version_diffs(audit_id_new);

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------
ALTER TABLE public.papers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rounds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.verdicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.debrief_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.final_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.version_diffs ENABLE ROW LEVEL SECURITY;

-- Authenticated clients are intentionally read-only. All mutations flow
-- through authenticated FastAPI endpoints and the backend-only worker identity;
-- this prevents an account owner from fabricating trusted verdict/report rows
-- through the public PostgREST API while RLS still enforces every read.
-- Re-running this migration replaces both the original FOR ALL policies and the
-- read-only policies below.
DROP POLICY IF EXISTS papers_owner_access ON public.papers;
DROP POLICY IF EXISTS papers_owner_select ON public.papers;
CREATE POLICY papers_owner_select ON public.papers
    FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS audits_owner_access ON public.audits;
DROP POLICY IF EXISTS audits_owner_select ON public.audits;
CREATE POLICY audits_owner_select ON public.audits
    FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS chunks_via_paper_owner ON public.chunks;
DROP POLICY IF EXISTS chunks_via_paper_owner_select ON public.chunks;
CREATE POLICY chunks_via_paper_owner_select ON public.chunks
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.papers AS owner_paper
            WHERE owner_paper.id = chunks.paper_id
              AND owner_paper.user_id = (SELECT auth.uid())
        )
    );

DROP POLICY IF EXISTS rounds_via_audit_owner ON public.rounds;
DROP POLICY IF EXISTS rounds_via_audit_owner_select ON public.rounds;
CREATE POLICY rounds_via_audit_owner_select ON public.rounds
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.audits AS owner_audit
            WHERE owner_audit.id = rounds.audit_id
              AND owner_audit.user_id = (SELECT auth.uid())
        )
    );

DROP POLICY IF EXISTS turns_via_audit_owner ON public.turns;
DROP POLICY IF EXISTS turns_via_audit_owner_select ON public.turns;
CREATE POLICY turns_via_audit_owner_select ON public.turns
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.rounds AS owner_round
            JOIN public.audits AS owner_audit ON owner_audit.id = owner_round.audit_id
            WHERE owner_round.id = turns.round_id
              AND owner_audit.user_id = (SELECT auth.uid())
        )
    );

DROP POLICY IF EXISTS verdicts_via_audit_owner ON public.verdicts;
DROP POLICY IF EXISTS verdicts_via_audit_owner_select ON public.verdicts;
CREATE POLICY verdicts_via_audit_owner_select ON public.verdicts
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.rounds AS owner_round
            JOIN public.audits AS owner_audit ON owner_audit.id = owner_round.audit_id
            WHERE owner_round.id = verdicts.round_id
              AND owner_audit.user_id = (SELECT auth.uid())
        )
    );

DROP POLICY IF EXISTS debrief_cards_via_audit_owner ON public.debrief_cards;
DROP POLICY IF EXISTS debrief_cards_via_audit_owner_select ON public.debrief_cards;
CREATE POLICY debrief_cards_via_audit_owner_select ON public.debrief_cards
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1
            FROM public.rounds AS owner_round
            JOIN public.audits AS owner_audit ON owner_audit.id = owner_round.audit_id
            WHERE owner_round.id = debrief_cards.round_id
              AND owner_audit.user_id = (SELECT auth.uid())
        )
    );

DROP POLICY IF EXISTS final_reports_via_audit_owner ON public.final_reports;
DROP POLICY IF EXISTS final_reports_via_audit_owner_select ON public.final_reports;
CREATE POLICY final_reports_via_audit_owner_select ON public.final_reports
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.audits AS owner_audit
            WHERE owner_audit.id = final_reports.audit_id
              AND owner_audit.user_id = (SELECT auth.uid())
        )
    );

DROP POLICY IF EXISTS version_diffs_via_both_audit_owners ON public.version_diffs;
DROP POLICY IF EXISTS version_diffs_via_both_audit_owners_select ON public.version_diffs;
CREATE POLICY version_diffs_via_both_audit_owners_select ON public.version_diffs
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.audits AS old_audit
            WHERE old_audit.id = version_diffs.audit_id_old
              AND old_audit.user_id = (SELECT auth.uid())
        )
        AND EXISTS (
            SELECT 1 FROM public.audits AS new_audit
            WHERE new_audit.id = version_diffs.audit_id_new
              AND new_audit.user_id = (SELECT auth.uid())
        )
    );

-- No anonymous PostgREST access. Service-role keeps its intentional BYPASSRLS;
-- authenticated requests receive read privileges and remain policy constrained.
REVOKE ALL ON TABLE
    public.papers,
    public.chunks,
    public.audits,
    public.rounds,
    public.turns,
    public.verdicts,
    public.debrief_cards,
    public.final_reports,
    public.version_diffs
FROM anon;

REVOKE ALL ON TABLE
    public.papers,
    public.chunks,
    public.audits,
    public.rounds,
    public.turns,
    public.verdicts,
    public.debrief_cards,
    public.final_reports,
    public.version_diffs
FROM authenticated;

GRANT SELECT ON TABLE
    public.papers,
    public.chunks,
    public.audits,
    public.rounds,
    public.turns,
    public.verdicts,
    public.debrief_cards,
    public.final_reports,
    public.version_diffs
TO authenticated;

-- Vector search remains invoker-scoped, so chunks RLS applies to RPC calls too.
ALTER FUNCTION public.match_chunks(TEXT, UUID, INTEGER) SECURITY INVOKER;
REVOKE ALL ON FUNCTION public.match_chunks(TEXT, UUID, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.match_chunks(TEXT, UUID, INTEGER) FROM anon;
GRANT EXECUTE ON FUNCTION public.match_chunks(TEXT, UUID, INTEGER)
    TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Private paper storage
-- Object names are always: <auth user UUID>/<paper UUID>.pdf
-- ---------------------------------------------------------------------------
INSERT INTO storage.buckets (id, name, public)
VALUES ('papers', 'papers', false)
ON CONFLICT (id) DO UPDATE SET public = false;

DROP POLICY IF EXISTS "Public Access" ON storage.objects;
DROP POLICY IF EXISTS papers_storage_owner_select ON storage.objects;
DROP POLICY IF EXISTS papers_storage_owner_insert ON storage.objects;
DROP POLICY IF EXISTS papers_storage_owner_update ON storage.objects;
DROP POLICY IF EXISTS papers_storage_owner_delete ON storage.objects;

CREATE POLICY papers_storage_owner_select ON storage.objects
    FOR SELECT TO authenticated
    USING (
        bucket_id = 'papers'
        AND (storage.foldername(name))[1] = (SELECT auth.uid())::TEXT
    );

-- No authenticated INSERT/UPDATE/DELETE policy is created. Validated uploads
-- and cleanup are performed only by the backend service identity.
REVOKE ALL ON TABLE storage.objects FROM anon;
REVOKE ALL ON TABLE storage.objects FROM authenticated;
GRANT SELECT ON TABLE storage.objects TO authenticated;

COMMIT;
