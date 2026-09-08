-- RentBuster — database schema
-- Run against any Postgres 13+ instance to create tables.
-- From the CLI: `python -m rentbuster init-db`

-- ── Core listings ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS listings (
    id                       SERIAL PRIMARY KEY,

    -- Identity
    source                   TEXT NOT NULL,         -- 'pararius' | 'rentbuster_nl'
    source_id                TEXT NOT NULL,
    url                      TEXT,

    -- Address
    street                   TEXT,
    house_number             TEXT,
    house_number_addition    TEXT,
    postal_code              TEXT,
    city                     TEXT,
    neighborhood             TEXT,
    address_key              TEXT,                  -- normalized for dedup

    -- Property
    asking_rent              INTEGER,               -- EUR/month
    surface_area_m2          INTEGER,
    num_rooms                SMALLINT,
    energy_label             TEXT,                  -- 'A', 'B', ..., 'A++++'
    construction_year        SMALLINT,
    property_type            TEXT,
    interior                 TEXT,
    description              TEXT,
    images                   JSONB,
    available_from           TEXT,
    agency_name              TEXT,

    -- WWS scoring
    wws_points               REAL,
    wws_max_rent             REAL,
    wws_savings              REAL,
    wws_is_bustable          BOOLEAN DEFAULT FALSE,
    wws_confidence           TEXT,                  -- 'HIGH' | 'MEDIUM' | 'LOW' | 'VERY_LOW'
    wws_breakdown            JSONB,
    wws_flags                JSONB,

    -- WOZ data
    woz_value                INTEGER,               -- EUR
    woz_reference_date       TEXT,
    woz_verified             BOOLEAN DEFAULT FALSE,

    -- Tenant suitability
    suitable_for_students    BOOLEAN,
    suitable_for_sharing     BOOLEAN,
    guarantor_accepted       BOOLEAN,

    -- Rent-buster.nl cross-reference (their own calculation for the same ad)
    rb_points                REAL,
    rb_estimated_max_rent    REAL,
    rb_savings               REAL,
    rb_confidence            TEXT,

    -- Bust score
    bust_score               REAL DEFAULT 0,

    -- Timestamps
    first_seen_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    disappeared_at           TIMESTAMPTZ,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT listings_source_id UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_source          ON listings (source);
CREATE INDEX IF NOT EXISTS idx_listings_city            ON listings (city);
CREATE INDEX IF NOT EXISTS idx_listings_address_key     ON listings (address_key);
CREATE INDEX IF NOT EXISTS idx_listings_asking_rent     ON listings (asking_rent);
CREATE INDEX IF NOT EXISTS idx_listings_wws_bustable    ON listings (wws_is_bustable) WHERE wws_is_bustable = TRUE;
CREATE INDEX IF NOT EXISTS idx_listings_wws_points      ON listings (wws_points);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen      ON listings (first_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_last_seen       ON listings (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_active          ON listings (disappeared_at) WHERE disappeared_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_listings_bust_score      ON listings (bust_score DESC) WHERE wws_is_bustable = TRUE;

-- ── WOZ cache ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS woz_cache (
    postal_code              TEXT NOT NULL,
    house_number             TEXT NOT NULL,
    house_number_addition    TEXT NOT NULL DEFAULT '',
    woz_value                INTEGER NOT NULL,
    reference_date           TEXT,
    verified                 BOOLEAN DEFAULT FALSE,
    source                   TEXT,                  -- 'kadaster' | 'estimated'
    fetched_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (postal_code, house_number, house_number_addition)
);

-- ── Telegram subscribers ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS telegram_subscribers (
    chat_id       BIGINT PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Dropped listings (per-subscriber hide list) ─────────────────────

CREATE TABLE IF NOT EXISTS dropped_listings (
    chat_id    BIGINT NOT NULL,
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    dropped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, source, source_id)
);

-- ── Scraper run log ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS scrape_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    source          TEXT,
    total_found     INTEGER DEFAULT 0,
    new_found       INTEGER DEFAULT 0,
    bustable_found  INTEGER DEFAULT 0,
    errors          TEXT
);
