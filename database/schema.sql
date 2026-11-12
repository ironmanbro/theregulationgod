-- India Capital RegHub — PostgreSQL schema (requires pgvector)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$ BEGIN
  CREATE TYPE investment_route AS ENUM ('FDI', 'FPI', 'FVCI', 'GENERAL');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE impact_level AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS regulators (
    id            SMALLSERIAL PRIMARY KEY,
    code          VARCHAR(16)  NOT NULL UNIQUE,
    name          TEXT         NOT NULL,
    sector        TEXT         NOT NULL,
    website       TEXT         NOT NULL,
    circulars_url TEXT,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS regulations_master (
    id               BIGSERIAL PRIMARY KEY,
    regulator_id     SMALLINT     NOT NULL REFERENCES regulators(id) ON DELETE RESTRICT,
    reference_no     TEXT         NOT NULL,
    title            TEXT         NOT NULL,
    summary          TEXT,
    full_text        TEXT,
    doc_type         VARCHAR(32)  NOT NULL DEFAULT 'CIRCULAR',  -- CIRCULAR, NOTIFICATION, PRESS_NOTE, MASTER_DIRECTION, FAQ
    routes           investment_route[] NOT NULL DEFAULT ARRAY['GENERAL']::investment_route[],
    impact           impact_level NOT NULL DEFAULT 'MEDIUM',
    issue_date       DATE         NOT NULL,
    effective_date   DATE,
    source_url       TEXT         NOT NULL,
    pdf_url          TEXT,
    content_hash     CHAR(64)     NOT NULL,
    embedding        vector(384),
    search_tsv       tsvector GENERATED ALWAYS AS (
                        setweight(to_tsvector('english', coalesce(reference_no,'')), 'A') ||
                        setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
                        setweight(to_tsvector('english', coalesce(summary,'')), 'B') ||
                        setweight(to_tsvector('english', coalesce(full_text,'')), 'C')
                     ) STORED,
    scraped_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_regulator_ref UNIQUE (regulator_id, reference_no),
    CONSTRAINT uq_content_hash UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS ix_reg_master_regulator  ON regulations_master (regulator_id);
CREATE INDEX IF NOT EXISTS ix_reg_master_issue_date ON regulations_master (issue_date DESC);
CREATE INDEX IF NOT EXISTS ix_reg_master_routes     ON regulations_master USING GIN (routes);
CREATE INDEX IF NOT EXISTS ix_reg_master_impact     ON regulations_master (impact);
CREATE INDEX IF NOT EXISTS ix_reg_master_tsv        ON regulations_master USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS ix_reg_master_title_trgm ON regulations_master USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_reg_master_embedding  ON regulations_master USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS compliance_checklist (
    id            BIGSERIAL PRIMARY KEY,
    regulator_id  SMALLINT NOT NULL REFERENCES regulators(id) ON DELETE CASCADE,
    route         investment_route NOT NULL DEFAULT 'GENERAL',
    item          TEXT NOT NULL,
    is_complete   BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ,
    notes         TEXT,
    UNIQUE (regulator_id, route, item)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id            BIGSERIAL PRIMARY KEY,
    regulator_id  SMALLINT NOT NULL REFERENCES regulators(id),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    items_found   INT NOT NULL DEFAULT 0,
    items_new     INT NOT NULL DEFAULT 0,
    status        VARCHAR(16) NOT NULL DEFAULT 'RUNNING',
    error         TEXT
);

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reg_master_updated ON regulations_master;
CREATE TRIGGER trg_reg_master_updated BEFORE UPDATE ON regulations_master
FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

INSERT INTO regulators (code, name, sector, website, circulars_url) VALUES
 ('RBI',    'Reserve Bank of India',                              'Banking / FEMA',        'https://www.rbi.org.in',        'https://www.rbi.org.in/Scripts/BS_CircularIndexDisplay.aspx'),
 ('SEBI',   'Securities and Exchange Board of India',             'Capital Markets',       'https://www.sebi.gov.in',       'https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0'),
 ('DPIIT',  'Department for Promotion of Industry and Internal Trade','FDI Policy',        'https://dpiit.gov.in',          'https://dpiit.gov.in/policies-rules-and-acts/press-notes-fdi-circular'),
 ('DEA',    'Department of Economic Affairs',                     'Finance Ministry',      'https://dea.gov.in',            'https://dea.gov.in/notifications'),
 ('MCA',    'Ministry of Corporate Affairs',                      'Corporate Law',         'https://www.mca.gov.in',        'https://www.mca.gov.in/content/mca/global/en/acts-rules/ebooks/notifications.html'),
 ('CCI',    'Competition Commission of India',                    'Competition / M&A',     'https://www.cci.gov.in',        'https://www.cci.gov.in/legal-framwork/regulations'),
 ('CBDT',   'Central Board of Direct Taxes',                      'Direct Tax',            'https://incometaxindia.gov.in', 'https://incometaxindia.gov.in/Pages/communications/notifications.aspx'),
 ('IFSCA',  'International Financial Services Centres Authority', 'GIFT City / IFSC',      'https://ifsca.gov.in',          'https://ifsca.gov.in/Legal/Index/wF2N2Ebbx0w='),
 ('NSDL',   'National Securities Depository Limited',             'Depository',            'https://nsdl.co.in',            'https://nsdl.co.in/circulars.php'),
 ('CDSL',   'Central Depository Services Limited',                'Depository',            'https://www.cdslindia.com',     'https://www.cdslindia.com/Publications/Communique.aspx'),
 ('NSE',    'National Stock Exchange of India',                   'Stock Exchange',        'https://www.nseindia.com',      'https://www.nseindia.com/resources/exchange-communication-circulars'),
 ('BSE',    'BSE Limited',                                        'Stock Exchange',        'https://www.bseindia.com',      'https://www.bseindia.com/markets/MarketInfo/NoticesCirculars.aspx'),
 ('IRDAI',  'Insurance Regulatory and Development Authority of India','Insurance',         'https://irdai.gov.in',          'https://irdai.gov.in/circulars'),
 ('PFRDA',  'Pension Fund Regulatory and Development Authority',  'Pensions',              'https://www.pfrda.org.in',      'https://www.pfrda.org.in/index1.cshtml?lsid=1150'),
 ('TRAI',   'Telecom Regulatory Authority of India',              'Telecom',               'https://www.trai.gov.in',       'https://www.trai.gov.in/notifications/regulation'),
 ('DoT',    'Department of Telecommunications',                   'Telecom',               'https://dot.gov.in',            'https://dot.gov.in/whatsnew'),
 ('MIB',    'Ministry of Information and Broadcasting',           'Media / Broadcasting',  'https://mib.gov.in',            'https://mib.gov.in/acts-and-rules'),
 ('CDSCO',  'Central Drugs Standard Control Organisation',        'Pharma / Med Devices',  'https://cdsco.gov.in',          'https://cdsco.gov.in/opencms/opencms/en/Notifications/'),
 ('DGCA',   'Directorate General of Civil Aviation',              'Civil Aviation',        'https://www.dgca.gov.in',       'https://www.dgca.gov.in/digigov-portal/'),
 ('PNGRB',  'Petroleum and Natural Gas Regulatory Board',         'Oil & Gas',             'https://www.pngrb.gov.in',      'https://www.pngrb.gov.in/eng-web/notification.html'),
 ('CERC',   'Central Electricity Regulatory Commission',          'Power',                 'https://cercind.gov.in',        'https://cercind.gov.in/regulations.html'),
 ('FSSAI',  'Food Safety and Standards Authority of India',       'Food',                  'https://www.fssai.gov.in',      'https://www.fssai.gov.in/notifications.php'),
 ('AAI',    'Airports Authority of India',                        'Airports',              'https://www.aai.aero',          'https://www.aai.aero/en/tender'),
 ('AERB',   'Atomic Energy Regulatory Board',                     'Nuclear / Radiation',   'https://www.aerb.gov.in',       'https://www.aerb.gov.in/english/regulatory-documents'),
 ('NABARD', 'National Bank for Agriculture and Rural Development','Rural / Agri Finance',  'https://www.nabard.org',        'https://www.nabard.org/circulars.aspx')
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, sector = EXCLUDED.sector,
  website = EXCLUDED.website, circulars_url = EXCLUDED.circulars_url;

-- Default due-diligence checklist: one item per regulator
INSERT INTO compliance_checklist (regulator_id, route, item)
SELECT r.id, 'GENERAL', v.item
FROM (VALUES
 ('RBI',    'FC-GPR / FC-TRS filed on FIRMS within timelines'),
 ('SEBI',   'FPI/FVCI registration & KYC current'),
 ('DPIIT',  'Sectoral cap & route verified (Press Notes)'),
 ('DEA',    'NDI Rules / pricing compliance confirmed'),
 ('MCA',    'Allotment returns (PAS-3) & beneficial ownership filed'),
 ('CCI',    'Combination notification threshold assessed'),
 ('CBDT',   'Withholding tax / treaty eligibility (TRC, Form 10F)'),
 ('IFSCA',  'IFSC unit licensing reviewed'),
 ('NSDL',   'Demat & ISIN for foreign holding set up'),
 ('CDSL',   'Foreign investment limit monitoring'),
 ('NSE',    'Listing / disclosure obligations reviewed'),
 ('BSE',    'Shareholding pattern disclosures'),
 ('IRDAI',  'Insurance FDI cap & Indian control test'),
 ('PFRDA',  'Pension sector FDI cap check'),
 ('TRAI',   'Telecom licence conditions reviewed'),
 ('DoT',    'Security clearance for telecom FDI'),
 ('MIB',    'Broadcasting uplink/downlink approval'),
 ('CDSCO',  'Pharma brownfield approval assessed'),
 ('DGCA',   'Air operator permit / SPOC compliance'),
 ('PNGRB',  'Authorisation for gas infrastructure'),
 ('CERC',   'Power trading licence implications'),
 ('FSSAI',  'Food business licence (FBO) obtained'),
 ('AAI',    'Airport concession / approvals reviewed'),
 ('AERB',   'Radiation facility licence checked'),
 ('NABARD', 'Agri/rural financing norms reviewed')
) AS v(code, item)
JOIN regulators r ON r.code = v.code
ON CONFLICT (regulator_id, route, item) DO NOTHING;

-- Per-deal checklists: compliance_checklist holds the item templates;
-- deal_checklist holds each deal's progress (a missing row means not complete).
CREATE TABLE IF NOT EXISTS deals (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    route       investment_route NOT NULL DEFAULT 'GENERAL',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deal_checklist (
    deal_id       BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    item_id       BIGINT NOT NULL REFERENCES compliance_checklist(id) ON DELETE CASCADE,
    is_complete   BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ,
    notes         TEXT,
    PRIMARY KEY (deal_id, item_id)
);

-- Route-specific checklist items (shown only to deals on that route, alongside GENERAL items)
INSERT INTO compliance_checklist (regulator_id, route, item)
SELECT r.id, v.route::investment_route, v.item
FROM (VALUES
 ('DPIIT', 'FDI',  'Automatic vs government approval route confirmed for the sector'),
 ('DPIIT', 'FDI',  'Press Note 3 (2020) land-border country beneficial ownership check'),
 ('RBI',   'FDI',  'Pricing guidelines met; valuation certificate obtained'),
 ('RBI',   'FDI',  'Funds received through AD Category-I bank; KYC report obtained'),
 ('MCA',   'FDI',  'Board/shareholder approvals for allotment recorded'),
 ('SEBI',  'FPI',  'FPI category and Designated Depository Participant confirmed'),
 ('SEBI',  'FPI',  'Per-FPI and aggregate investment limits in the company checked'),
 ('SEBI',  'FPI',  'Granular beneficial ownership disclosures assessed'),
 ('NSDL',  'FPI',  'Custodian and demat account set up for FPI holdings'),
 ('CBDT',  'FPI',  'Capital gains tax treatment and PAN for the FPI confirmed'),
 ('SEBI',  'FVCI', 'FVCI registration under SEBI (FVCI) Regulations confirmed'),
 ('SEBI',  'FVCI', 'Investee company is in a sector permitted for FVCI'),
 ('RBI',   'FVCI', 'Investment reporting through AD Category-I bank completed'),
 ('CBDT',  'FVCI', 'Tax treatment of FVCI returns and treaty eligibility reviewed')
) AS v(code, route, item)
JOIN regulators r ON r.code = v.code
ON CONFLICT (regulator_id, route, item) DO NOTHING;

-- Sector scoping: a deal's checklist shows core regulators plus those for its sectors.
-- deals.sectors NULL means "not set" and shows all 25 regulators.
ALTER TABLE regulators ADD COLUMN IF NOT EXISTS is_core BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE regulators SET is_core = code IN
  ('RBI','SEBI','DPIIT','DEA','MCA','CCI','CBDT','IFSCA','NSDL','CDSL','NSE','BSE');

CREATE TABLE IF NOT EXISTS sector_regulators (
    sector        VARCHAR(32) NOT NULL,
    label         TEXT        NOT NULL,
    regulator_id  SMALLINT    NOT NULL REFERENCES regulators(id) ON DELETE CASCADE,
    PRIMARY KEY (sector, regulator_id)
);

INSERT INTO sector_regulators (sector, label, regulator_id)
SELECT v.sector, v.label, r.id
FROM (VALUES
 ('INSURANCE', 'Insurance',                'IRDAI'),
 ('PENSION',   'Pensions',                 'PFRDA'),
 ('TELECOM',   'Telecom',                  'TRAI'),
 ('TELECOM',   'Telecom',                  'DoT'),
 ('MEDIA',     'Media & Broadcasting',     'MIB'),
 ('PHARMA',    'Pharma & Medical Devices', 'CDSCO'),
 ('AVIATION',  'Aviation & Airports',      'DGCA'),
 ('AVIATION',  'Aviation & Airports',      'AAI'),
 ('OIL_GAS',   'Oil & Gas',                'PNGRB'),
 ('POWER',     'Power',                    'CERC'),
 ('FOOD',      'Food',                     'FSSAI'),
 ('NUCLEAR',   'Nuclear & Radiation',      'AERB'),
 ('AGRI',      'Agri & Rural Finance',     'NABARD')
) AS v(sector, label, code)
JOIN regulators r ON r.code = v.code
ON CONFLICT (sector, regulator_id) DO UPDATE SET label = EXCLUDED.label;

ALTER TABLE deals ADD COLUMN IF NOT EXISTS sectors TEXT[];

-- IFSCA is core (no longer a sector); clean up databases that had the old IFSC sector.
DELETE FROM sector_regulators WHERE sector = 'IFSC';
UPDATE deals SET sectors = array_remove(sectors, 'IFSC') WHERE 'IFSC' = ANY(sectors);
