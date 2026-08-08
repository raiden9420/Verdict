-- Add reproducibility_signals column to papers table
ALTER TABLE papers ADD COLUMN IF NOT EXISTS reproducibility_signals JSONB;
