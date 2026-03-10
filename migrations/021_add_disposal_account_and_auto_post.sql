-- ============================================================
-- Migration 021: Add disposal account + auto_post to models/assets
-- ============================================================

BEGIN;

-- 1. asset_models: disposal account + auto-post toggle
ALTER TABLE fixed_assets.asset_models
    ADD COLUMN IF NOT EXISTS account_disposal_number VARCHAR(20),
    ADD COLUMN IF NOT EXISTS auto_post_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. assets: disposal account (copied from model)
ALTER TABLE fixed_assets.assets
    ADD COLUMN IF NOT EXISTS account_disposal_number VARCHAR(20);

COMMIT;
