-- ============================================================
-- Migration 023: Create erp_data schema (Contacts + Invoices + Aging)
-- Internalisation des donnees ERP (sync hash + time overlap)
-- Pattern identique a accounting.gl_entries et fixed_assets.*
-- ============================================================

BEGIN;

-- ============================================================
-- SCHEMA
-- ============================================================
CREATE SCHEMA IF NOT EXISTS erp_data;

-- ============================================================
-- FUNCTION: updated_at trigger (reusable)
-- ============================================================
CREATE OR REPLACE FUNCTION erp_data.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- TABLE: erp_data.contacts
-- Source ERP: res.partner (Odoo)
-- Sync: hash SHA-256 + time overlap sur write_date
-- ============================================================
CREATE TABLE erp_data.contacts (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id          UUID NOT NULL REFERENCES core.companies(id),

    -- Identification ERP
    erp_partner_id      INTEGER NOT NULL,
    erp_type            VARCHAR(20) NOT NULL DEFAULT 'odoo',

    -- Identite
    name                VARCHAR(255) NOT NULL,
    display_name        VARCHAR(255),
    is_company          BOOLEAN NOT NULL DEFAULT TRUE,
    parent_id           INTEGER,

    -- Classification
    supplier_rank       INTEGER NOT NULL DEFAULT 0,
    customer_rank       INTEGER NOT NULL DEFAULT 0,
    contact_type        VARCHAR(20) GENERATED ALWAYS AS (
        CASE
            WHEN supplier_rank > 0 AND customer_rank > 0 THEN 'both'
            WHEN supplier_rank > 0 THEN 'supplier'
            WHEN customer_rank > 0 THEN 'customer'
            ELSE 'other'
        END
    ) STORED,

    -- Coordonnees
    email               VARCHAR(255),
    phone               VARCHAR(50),
    mobile              VARCHAR(50),
    website             VARCHAR(500),

    -- Adresse
    street              VARCHAR(255),
    street2             VARCHAR(255),
    city                VARCHAR(100),
    zip                 VARCHAR(20),
    state_code          VARCHAR(10),
    state_name          VARCHAR(100),
    country_code        CHAR(2),
    country_name        VARCHAR(100),

    -- Fiscal
    vat                 VARCHAR(50),

    -- Comptes comptables (account_number du COA, pas IDs ERP)
    property_account_payable      VARCHAR(20),
    property_account_receivable   VARCHAR(20),

    -- Termes de paiement
    payment_term_supplier   VARCHAR(100),
    payment_term_customer   VARCHAR(100),

    -- Controle sync
    sync_hash           VARCHAR(64) NOT NULL,
    sync_version        INTEGER NOT NULL DEFAULT 1,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE,
    last_erp_write_date TIMESTAMPTZ,

    -- Metadata
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (company_id, erp_partner_id)
);

CREATE INDEX idx_contacts_company ON erp_data.contacts(company_id);
CREATE INDEX idx_contacts_type ON erp_data.contacts(company_id, contact_type);
CREATE INDEX idx_contacts_name ON erp_data.contacts(company_id, name);
CREATE INDEX idx_contacts_hash ON erp_data.contacts(company_id, sync_hash);
CREATE INDEX idx_contacts_active ON erp_data.contacts(company_id, is_active, is_deleted);
CREATE INDEX idx_contacts_erp_write ON erp_data.contacts(company_id, last_erp_write_date);

CREATE TRIGGER trg_contacts_updated_at
    BEFORE UPDATE ON erp_data.contacts
    FOR EACH ROW EXECUTE FUNCTION erp_data.set_updated_at();

-- ============================================================
-- TABLE: erp_data.invoices
-- Source ERP: account.move (Odoo)
-- Types: in_invoice (AP), out_invoice (AR), in_refund, out_refund
-- ============================================================
CREATE TABLE erp_data.invoices (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id          UUID NOT NULL REFERENCES core.companies(id),

    -- Identification ERP
    erp_move_id         INTEGER NOT NULL,
    erp_type            VARCHAR(20) NOT NULL DEFAULT 'odoo',

    -- Classification
    move_type           VARCHAR(20) NOT NULL
                        CHECK (move_type IN (
                            'in_invoice', 'out_invoice',
                            'in_refund', 'out_refund',
                            'entry'
                        )),
    invoice_direction   VARCHAR(10) GENERATED ALWAYS AS (
        CASE
            WHEN move_type IN ('in_invoice', 'in_refund') THEN 'payable'
            WHEN move_type IN ('out_invoice', 'out_refund') THEN 'receivable'
            ELSE 'other'
        END
    ) STORED,

    -- Reference
    invoice_name        VARCHAR(100) NOT NULL,
    invoice_ref         VARCHAR(255),
    invoice_origin      VARCHAR(255),

    -- Contact
    contact_id          UUID REFERENCES erp_data.contacts(id) ON DELETE SET NULL,
    erp_partner_id      INTEGER,
    partner_name        VARCHAR(255),

    -- Dates
    invoice_date        DATE,
    invoice_date_due    DATE,
    accounting_date     DATE,

    -- Montants
    amount_untaxed      NUMERIC(15,2) NOT NULL DEFAULT 0,
    amount_tax          NUMERIC(15,2) NOT NULL DEFAULT 0,
    amount_total        NUMERIC(15,2) NOT NULL DEFAULT 0,
    amount_residual     NUMERIC(15,2) NOT NULL DEFAULT 0,
    amount_total_signed NUMERIC(15,2) NOT NULL DEFAULT 0,
    amount_residual_signed NUMERIC(15,2) NOT NULL DEFAULT 0,
    currency            CHAR(3) NOT NULL DEFAULT 'CHF',

    -- Montants en devise de la societe (pour aging homogene)
    amount_total_company    NUMERIC(15,2),
    amount_residual_company NUMERIC(15,2),

    -- Statut
    state               VARCHAR(20) NOT NULL DEFAULT 'draft'
                        CHECK (state IN ('draft', 'posted', 'cancel')),
    payment_state       VARCHAR(20) NOT NULL DEFAULT 'not_paid'
                        CHECK (payment_state IN (
                            'not_paid', 'partial', 'paid',
                            'in_payment', 'reversed', 'invoicing_legacy'
                        )),

    -- Journal
    journal_code        VARCHAR(10),
    journal_name        VARCHAR(255),

    -- Controle sync
    sync_hash           VARCHAR(64) NOT NULL,
    sync_version        INTEGER NOT NULL DEFAULT 1,
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE,
    last_erp_write_date TIMESTAMPTZ,

    -- Metadata
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (company_id, erp_move_id)
);

CREATE INDEX idx_invoices_company ON erp_data.invoices(company_id);
CREATE INDEX idx_invoices_contact ON erp_data.invoices(contact_id);
CREATE INDEX idx_invoices_partner ON erp_data.invoices(company_id, erp_partner_id);
CREATE INDEX idx_invoices_type ON erp_data.invoices(company_id, move_type);
CREATE INDEX idx_invoices_direction ON erp_data.invoices(company_id, invoice_direction);
CREATE INDEX idx_invoices_state ON erp_data.invoices(company_id, state, payment_state);
CREATE INDEX idx_invoices_date ON erp_data.invoices(company_id, invoice_date);
CREATE INDEX idx_invoices_due ON erp_data.invoices(company_id, invoice_date_due);
CREATE INDEX idx_invoices_hash ON erp_data.invoices(company_id, sync_hash);
CREATE INDEX idx_invoices_erp_write ON erp_data.invoices(company_id, last_erp_write_date);

-- Index partiel: factures ouvertes uniquement (requete frequente pour aging)
CREATE INDEX idx_invoices_open ON erp_data.invoices(company_id, invoice_direction, invoice_date_due)
    WHERE state = 'posted' AND payment_state IN ('not_paid', 'partial');

CREATE TRIGGER trg_invoices_updated_at
    BEFORE UPDATE ON erp_data.invoices
    FOR EACH ROW EXECUTE FUNCTION erp_data.set_updated_at();

-- ============================================================
-- TABLE: erp_data.invoice_lines
-- Source ERP: account.move.line (Odoo)
-- ============================================================
CREATE TABLE erp_data.invoice_lines (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    invoice_id          UUID NOT NULL REFERENCES erp_data.invoices(id) ON DELETE CASCADE,
    company_id          UUID NOT NULL,

    -- Identification ERP
    erp_line_id         INTEGER NOT NULL,
    erp_move_id         INTEGER NOT NULL,

    -- Contenu
    line_name           VARCHAR(500),
    quantity            NUMERIC(15,4) NOT NULL DEFAULT 1,
    price_unit          NUMERIC(15,4) NOT NULL DEFAULT 0,
    discount            NUMERIC(5,2) NOT NULL DEFAULT 0,

    -- Comptabilite
    account_number      VARCHAR(20),
    account_name        VARCHAR(255),

    -- Montants
    price_subtotal      NUMERIC(15,2) NOT NULL DEFAULT 0,
    price_total         NUMERIC(15,2) NOT NULL DEFAULT 0,
    debit               NUMERIC(15,2) NOT NULL DEFAULT 0,
    credit              NUMERIC(15,2) NOT NULL DEFAULT 0,
    balance             NUMERIC(15,2) NOT NULL DEFAULT 0,

    -- TVA
    tax_ids             JSONB DEFAULT '[]',
    tax_rate            NUMERIC(5,2),

    -- Produit (optionnel)
    product_name        VARCHAR(255),
    product_id          INTEGER,

    -- Controle sync
    sync_hash           VARCHAR(64) NOT NULL,

    -- Metadata
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (company_id, erp_line_id)
);

CREATE INDEX idx_invoice_lines_invoice ON erp_data.invoice_lines(invoice_id);
CREATE INDEX idx_invoice_lines_account ON erp_data.invoice_lines(company_id, account_number);
CREATE INDEX idx_invoice_lines_erp_move ON erp_data.invoice_lines(company_id, erp_move_id);

-- ============================================================
-- TABLE: erp_data.aging_snapshots
-- Table materialisee, recalculee par l'aging engine
-- Un snapshot = une photo de la balance agee a une date donnee
-- ============================================================
CREATE TABLE erp_data.aging_snapshots (
    id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id          UUID NOT NULL REFERENCES core.companies(id),

    -- Parametres du snapshot
    snapshot_date       DATE NOT NULL,
    direction           VARCHAR(10) NOT NULL
                        CHECK (direction IN ('payable', 'receivable')),

    -- Contact
    contact_id          UUID REFERENCES erp_data.contacts(id) ON DELETE SET NULL,
    erp_partner_id      INTEGER,
    partner_name        VARCHAR(255),

    -- Devise du report (devise societe)
    currency            CHAR(3) NOT NULL DEFAULT 'CHF',

    -- Buckets d'aging (montant residuel en devise societe)
    not_due             NUMERIC(15,2) NOT NULL DEFAULT 0,
    bucket_1_30         NUMERIC(15,2) NOT NULL DEFAULT 0,
    bucket_31_60        NUMERIC(15,2) NOT NULL DEFAULT 0,
    bucket_61_90        NUMERIC(15,2) NOT NULL DEFAULT 0,
    bucket_91_120       NUMERIC(15,2) NOT NULL DEFAULT 0,
    bucket_120_plus     NUMERIC(15,2) NOT NULL DEFAULT 0,
    total               NUMERIC(15,2) NOT NULL DEFAULT 0,

    -- Nombre de factures par bucket
    count_not_due       INTEGER NOT NULL DEFAULT 0,
    count_1_30          INTEGER NOT NULL DEFAULT 0,
    count_31_60         INTEGER NOT NULL DEFAULT 0,
    count_61_90         INTEGER NOT NULL DEFAULT 0,
    count_91_120        INTEGER NOT NULL DEFAULT 0,
    count_120_plus      INTEGER NOT NULL DEFAULT 0,
    count_total         INTEGER NOT NULL DEFAULT 0,

    -- Metadata
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (company_id, snapshot_date, direction, contact_id)
);

CREATE INDEX idx_aging_company_date ON erp_data.aging_snapshots(company_id, snapshot_date, direction);
CREATE INDEX idx_aging_contact ON erp_data.aging_snapshots(contact_id);

-- ============================================================
-- VIEW: erp_data.open_invoices
-- Vue utilitaire: factures ouvertes pour l'aging engine
-- ============================================================
CREATE OR REPLACE VIEW erp_data.open_invoices AS
SELECT
    i.id,
    i.company_id,
    i.erp_move_id,
    i.move_type,
    i.invoice_direction,
    i.invoice_name,
    i.invoice_ref,
    i.contact_id,
    i.erp_partner_id,
    i.partner_name,
    i.invoice_date,
    i.invoice_date_due,
    i.amount_total,
    i.amount_residual,
    i.amount_total_signed,
    i.amount_residual_signed,
    i.amount_residual_company,
    i.currency,
    i.payment_state
FROM erp_data.invoices i
WHERE i.state = 'posted'
  AND i.payment_state IN ('not_paid', 'partial')
  AND i.is_deleted = FALSE;

-- ============================================================
-- VIEW: erp_data.contact_balances
-- Vue utilitaire: solde ouvert par contact (AP + AR)
-- ============================================================
CREATE OR REPLACE VIEW erp_data.contact_balances AS
SELECT
    c.id AS contact_id,
    c.company_id,
    c.name AS contact_name,
    c.contact_type,
    COALESCE(ap.total_residual, 0) AS ap_open_balance,
    COALESCE(ap.invoice_count, 0) AS ap_open_count,
    COALESCE(ar.total_residual, 0) AS ar_open_balance,
    COALESCE(ar.invoice_count, 0) AS ar_open_count,
    COALESCE(ap.total_residual, 0) + COALESCE(ar.total_residual, 0) AS net_balance
FROM erp_data.contacts c
LEFT JOIN LATERAL (
    SELECT
        SUM(ABS(i.amount_residual_signed)) AS total_residual,
        COUNT(*) AS invoice_count
    FROM erp_data.invoices i
    WHERE i.contact_id = c.id
      AND i.state = 'posted'
      AND i.payment_state IN ('not_paid', 'partial')
      AND i.invoice_direction = 'payable'
      AND i.is_deleted = FALSE
) ap ON TRUE
LEFT JOIN LATERAL (
    SELECT
        SUM(i.amount_residual_signed) AS total_residual,
        COUNT(*) AS invoice_count
    FROM erp_data.invoices i
    WHERE i.contact_id = c.id
      AND i.state = 'posted'
      AND i.payment_state IN ('not_paid', 'partial')
      AND i.invoice_direction = 'receivable'
      AND i.is_deleted = FALSE
) ar ON TRUE
WHERE c.is_deleted = FALSE
  AND c.is_active = TRUE;

COMMIT;
