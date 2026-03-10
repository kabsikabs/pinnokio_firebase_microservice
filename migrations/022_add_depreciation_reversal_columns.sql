-- Migration 022: Add reversal tracking to depreciation_lines
-- Allows reversing posted depreciation entries

ALTER TABLE fixed_assets.depreciation_lines
    ADD COLUMN IF NOT EXISTS is_reversed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS reversed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reversal_gl_entry_ref VARCHAR(100),
    ADD COLUMN IF NOT EXISTS reversal_entry_ref VARCHAR(100);

-- Index for quick lookup of reversed lines
CREATE INDEX IF NOT EXISTS idx_depr_lines_reversed
    ON fixed_assets.depreciation_lines(company_id, is_reversed)
    WHERE is_reversed = TRUE;
