-- ============================================================
-- Migration 020: Create fixed_assets schema
-- Module Immobilisations internalisé (multi-ERP)
-- ============================================================

BEGIN;

-- ============================================================
-- SCHEMA
-- ============================================================
CREATE SCHEMA IF NOT EXISTS fixed_assets;

-- ============================================================
-- TABLE: fixed_assets.asset_models (categories/templates)
-- Remplace account.asset (state='model') d'Odoo
-- PAS de journal_id (ecritures OD directes par l'engine)
-- PAS de distribution analytique
-- ============================================================
CREATE TABLE fixed_assets.asset_models (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id      UUID NOT NULL REFERENCES core.companies(id),

    -- Identification
    name            VARCHAR(255) NOT NULL,

    -- Comptes comptables (account_number du COA)
    -- Filtres par account_function:
    --   account_asset_number       → 'asset_fixed'
    --   account_depreciation_number → 'cumulated_depreciation'
    --   account_expense_number      → 'expense_depreciation'
    account_asset_number        VARCHAR(20) NOT NULL,
    account_depreciation_number VARCHAR(20) NOT NULL,
    account_expense_number      VARCHAR(20) NOT NULL,

    -- Methode d'amortissement
    method          VARCHAR(20) NOT NULL DEFAULT 'linear'
                    CHECK (method IN ('linear', 'degressive')),
    method_number   INTEGER NOT NULL DEFAULT 60,
    method_period   INTEGER NOT NULL DEFAULT 1
                    CHECK (method_period IN (1, 3, 6, 12)),
    prorata         BOOLEAN NOT NULL DEFAULT TRUE,

    -- Metadata
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (company_id, name)
);

CREATE INDEX idx_asset_models_company ON fixed_assets.asset_models(company_id);

-- ============================================================
-- TABLE: fixed_assets.assets (immobilisations actives)
-- ============================================================
CREATE TABLE fixed_assets.assets (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id      UUID NOT NULL REFERENCES core.companies(id),

    -- Identification
    name            VARCHAR(255) NOT NULL,
    reference       VARCHAR(100),
    model_id        UUID REFERENCES fixed_assets.asset_models(id) ON DELETE SET NULL,

    -- Valeurs
    acquisition_date    DATE NOT NULL,
    original_value      NUMERIC(15,2) NOT NULL CHECK (original_value > 0),
    salvage_value       NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (salvage_value >= 0),
    currency            CHAR(3) NOT NULL DEFAULT 'CHF',

    -- Comptes comptables (copies du model OU surcharges)
    account_asset_number        VARCHAR(20) NOT NULL,
    account_depreciation_number VARCHAR(20) NOT NULL,
    account_expense_number      VARCHAR(20) NOT NULL,

    -- Methode (copiee du model OU surchargee)
    method          VARCHAR(20) NOT NULL DEFAULT 'linear'
                    CHECK (method IN ('linear', 'degressive')),
    method_number   INTEGER NOT NULL DEFAULT 60,
    method_period   INTEGER NOT NULL DEFAULT 1
                    CHECK (method_period IN (1, 3, 6, 12)),
    prorata         BOOLEAN NOT NULL DEFAULT TRUE,
    first_depreciation_date DATE,

    -- Statut
    state           VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK (state IN ('draft', 'running', 'close', 'disposed')),

    -- Disposal
    disposal_date   DATE,
    disposal_value  NUMERIC(15,2),
    disposal_type   VARCHAR(20)
                    CHECK (disposal_type IS NULL OR disposal_type IN ('sale', 'scrap')),

    -- Lien facture d'origine
    source_invoice_ref  VARCHAR(100),
    source_document_id  VARCHAR(255),

    -- Metadata
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_assets_company ON fixed_assets.assets(company_id);
CREATE INDEX idx_assets_state ON fixed_assets.assets(company_id, state);
CREATE INDEX idx_assets_model ON fixed_assets.assets(model_id);
CREATE INDEX idx_assets_account ON fixed_assets.assets(company_id, account_asset_number);

-- ============================================================
-- TABLE: fixed_assets.depreciation_lines
-- ============================================================
CREATE TABLE fixed_assets.depreciation_lines (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    asset_id        UUID NOT NULL REFERENCES fixed_assets.assets(id) ON DELETE CASCADE,
    company_id      UUID NOT NULL,

    -- Planning
    line_number     INTEGER NOT NULL,
    depreciation_date DATE NOT NULL,
    amount          NUMERIC(15,2) NOT NULL,
    cumulated_amount NUMERIC(15,2) NOT NULL,
    remaining_value NUMERIC(15,2) NOT NULL,

    -- Ecriture comptable (rempli quand poste)
    is_posted       BOOLEAN NOT NULL DEFAULT FALSE,
    posted_date     TIMESTAMPTZ,
    gl_entry_ref    VARCHAR(100),

    -- Metadata
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (asset_id, line_number)
);

CREATE INDEX idx_depr_lines_asset ON fixed_assets.depreciation_lines(asset_id);
CREATE INDEX idx_depr_lines_date ON fixed_assets.depreciation_lines(company_id, depreciation_date);
CREATE INDEX idx_depr_lines_unposted ON fixed_assets.depreciation_lines(company_id, is_posted)
    WHERE is_posted = FALSE;

-- ============================================================
-- VIEW: fixed_assets.eligible_accounts
-- Comptes COA eligibles pour le mapping immobilisations
-- ============================================================
CREATE OR REPLACE VIEW fixed_assets.eligible_accounts AS
SELECT
    c.company_id,
    c.account_number,
    c.account_name,
    c.account_function,
    CASE c.account_function
        WHEN 'asset_fixed'              THEN 'asset'
        WHEN 'cumulated_depreciation'   THEN 'depreciation'
        WHEN 'expense_depreciation'     THEN 'expense'
    END AS role
FROM core.chart_of_accounts c
WHERE c.account_function IN ('asset_fixed', 'cumulated_depreciation', 'expense_depreciation')
  AND c.is_active = TRUE;

-- ============================================================
-- FUNCTION: updated_at trigger
-- ============================================================
CREATE OR REPLACE FUNCTION fixed_assets.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_asset_models_updated_at
    BEFORE UPDATE ON fixed_assets.asset_models
    FOR EACH ROW EXECUTE FUNCTION fixed_assets.set_updated_at();

CREATE TRIGGER trg_assets_updated_at
    BEFORE UPDATE ON fixed_assets.assets
    FOR EACH ROW EXECUTE FUNCTION fixed_assets.set_updated_at();

COMMIT;
