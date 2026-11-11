-- Phase 2 (spec 5): parsed areas keyed by sha256(areas_raw); rebuilt by `python -m outages areas`.

ALTER TABLE outages ADD COLUMN areas_hash TEXT;  -- sha256(areas_raw), kept by `areas`
CREATE INDEX outages_areas_hash ON outages(areas_hash);

CREATE TABLE area_parses (
    areas_hash     TEXT PRIMARY KEY,
    layout         TEXT NOT NULL
                   CHECK (layout IN ('city_first', 'barangay_first', 'structured', 'other')),
    towns_json     TEXT NOT NULL,
    warnings_json  TEXT NOT NULL DEFAULT '[]',
    extractor      TEXT NOT NULL,
    parsed_at      TEXT NOT NULL
);

CREATE TABLE area_barangays (
    areas_hash  TEXT NOT NULL REFERENCES area_parses(areas_hash) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    name_raw    TEXT NOT NULL,
    town_psgc   TEXT,
    psgc_code   TEXT,
    match       TEXT NOT NULL
                CHECK (match IN ('exact', 'alias', 'ambiguous', 'other_town', 'unmatched')),
    PRIMARY KEY (areas_hash, seq)
);
CREATE INDEX area_barangays_psgc ON area_barangays(psgc_code);

CREATE TABLE area_entities (
    areas_hash  TEXT NOT NULL REFERENCES area_parses(areas_hash) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL
                CHECK (kind IN ('street', 'sitio_purok', 'subdivision', 'establishment', 'other')),
    name        TEXT NOT NULL,
    extractor   TEXT NOT NULL,
    PRIMARY KEY (areas_hash, seq)
);

CREATE TABLE llm_cache (
    key             TEXT PRIMARY KEY,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    seconds         REAL NOT NULL,
    created_at      TEXT NOT NULL
);
