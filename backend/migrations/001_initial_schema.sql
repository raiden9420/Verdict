-- ============================================================
-- Verdict Phase 1 — Initial Schema
-- Run this in the Supabase SQL Editor (or via psql).
-- ============================================================

-- Enable pgvector extension (Supabase has it pre-installed)
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Papers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS papers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename      TEXT NOT NULL,
    storage_path  TEXT,
    page_count    INTEGER,
    session_id    TEXT,
    embedding_space TEXT,
    uploaded_at   TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Chunks — one per ~300-500 word segment of a paper
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id      UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section       TEXT,
    text          TEXT NOT NULL,
    embedding     vector(384) NOT NULL,
    page_number   INTEGER,
    chunk_index   INTEGER,
    UNIQUE(paper_id, chunk_index)
);

-- ---------------------------------------------------------------------------
-- Audits — one per user-initiated review session
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audits (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id          UUID NOT NULL REFERENCES papers(id),
    session_id        TEXT NOT NULL,
    round_topic       TEXT NOT NULL,
    strictness_level  TEXT DEFAULT 'standard',
    depth             TEXT DEFAULT 'fast',
    status            TEXT DEFAULT 'pending',
    error_message     TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Rounds — one per round within an audit (Phase 1: always 1 round)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rounds (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_id      UUID NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    round_number  INTEGER DEFAULT 1,
    topic         TEXT NOT NULL,
    status        TEXT DEFAULT 'in_progress'
);

-- ---------------------------------------------------------------------------
-- Turns — every agent utterance in a round
-- exchange_number groups one full Attacker→Defender→Validator→Referee cycle.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS turns (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id         UUID NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    exchange_number  INTEGER NOT NULL,
    agent_type       TEXT NOT NULL,   -- attacker | defender | referee | validator
    sequence         INTEGER NOT NULL,
    content          JSONB NOT NULL,
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Verdicts — one per exchange, written by the Referee
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verdicts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id         UUID NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    exchange_number  INTEGER NOT NULL,
    claim_summary    TEXT,
    verdict_type     TEXT NOT NULL,   -- SOLIDIFIED | ACTIONABLE_FLAW | CONTESTED
    confidence       FLOAT,
    rationale        TEXT,
    cited_chunk_ids  JSONB
);

-- ---------------------------------------------------------------------------
-- Debrief Cards — one per round, generated after all exchanges
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS debrief_cards (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id               UUID NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    executive_synthesis    TEXT,
    solidified_strengths   JSONB,
    actionable_weaknesses  JSONB,
    contested_points       JSONB,
    created_at             TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_chunks_paper      ON chunks(paper_id);
CREATE INDEX IF NOT EXISTS idx_papers_session     ON papers(session_id);
CREATE INDEX IF NOT EXISTS idx_audits_session     ON audits(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_round        ON turns(round_id, exchange_number, sequence);
CREATE INDEX IF NOT EXISTS idx_verdicts_round     ON verdicts(round_id, exchange_number);

-- ---------------------------------------------------------------------------
-- RPC: vector similarity search for chunk retrieval
-- Accepts embedding as a string (e.g. '[0.1, 0.2, ...]') and casts internally.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_chunks(
    query_embedding  TEXT,
    match_paper_id   UUID,
    match_count      INT DEFAULT 5
)
RETURNS TABLE (
    id           UUID,
    paper_id     UUID,
    section      TEXT,
    text         TEXT,
    page_number  INTEGER,
    chunk_index  INTEGER,
    similarity   FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.paper_id,
        c.section,
        c.text,
        c.page_number,
        c.chunk_index,
        (1 - (c.embedding <=> query_embedding::vector))::FLOAT AS similarity
    FROM chunks c
    WHERE c.paper_id = match_paper_id
    ORDER BY c.embedding <=> query_embedding::vector
    LIMIT match_count;
END;
$$;

-- Disable RLS for Phase 1 (no auth).
-- Phase 3 will enable RLS with proper user-scoped policies.
ALTER TABLE papers DISABLE ROW LEVEL SECURITY;
ALTER TABLE chunks DISABLE ROW LEVEL SECURITY;
ALTER TABLE audits DISABLE ROW LEVEL SECURITY;
ALTER TABLE rounds DISABLE ROW LEVEL SECURITY;
ALTER TABLE turns DISABLE ROW LEVEL SECURITY;
ALTER TABLE debrief_cards DISABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Storage
-- ---------------------------------------------------------------------------
-- Papers are private. The backend authorizes a browser session and issues a
-- short-lived signed URL; it must use a service-role/secret Supabase key.
INSERT INTO storage.buckets (id, name, public)
VALUES ('papers', 'papers', false)
ON CONFLICT (id) DO UPDATE SET public = false;
