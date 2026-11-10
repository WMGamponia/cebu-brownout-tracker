-- Phase 1 schema (spec 6.1). Outage times: ISO 8601 +08:00; bookkeeping: UTC ISO 8601.

CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE posts (
    id               INTEGER PRIMARY KEY,
    url              TEXT NOT NULL UNIQUE,
    slug             TEXT NOT NULL,
    title            TEXT,
    sitemap_lastmod  TEXT,
    first_seen_at    TEXT NOT NULL,
    last_fetched_at  TEXT,
    text_hash        TEXT,
    parse_status     TEXT NOT NULL DEFAULT 'pending'
                     CHECK (parse_status IN ('ok', 'failed', 'pending')),
    parse_error      TEXT
);

-- One row per distinct text_hash of a post; content_gz is the gzipped content-node HTML.
CREATE TABLE post_versions (
    id          INTEGER PRIMARY KEY,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    fetched_at  TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    content_gz  BLOB NOT NULL,
    UNIQUE (post_id, text_hash)
);

CREATE TABLE outages (
    id                     INTEGER PRIMARY KEY,
    post_id                INTEGER NOT NULL REFERENCES posts(id),
    seq                    INTEGER NOT NULL,
    heading_text           TEXT NOT NULL,
    start_at               TEXT NOT NULL,
    end_at                 TEXT NOT NULL,
    duration_label         TEXT,
    status                 TEXT NOT NULL
                           CHECK (status IN ('scheduled', 'cancelled', 'additional', 'removed')),
    purpose                TEXT NOT NULL,
    areas_raw              TEXT NOT NULL,
    areas_key              TEXT NOT NULL,
    map_image_url          TEXT,
    warnings_json          TEXT NOT NULL DEFAULT '[]',
    first_seen_version_id  INTEGER NOT NULL REFERENCES post_versions(id),
    last_seen_version_id   INTEGER NOT NULL REFERENCES post_versions(id)
);

CREATE TABLE outage_changes (
    id           INTEGER PRIMARY KEY,
    outage_id    INTEGER NOT NULL REFERENCES outages(id),
    version_id   INTEGER NOT NULL REFERENCES post_versions(id),
    kind         TEXT NOT NULL CHECK (kind IN ('added', 'cancelled', 'time_changed',
                                               'areas_changed', 'status_changed', 'removed')),
    old_json     TEXT,
    new_json     TEXT,
    detected_at  TEXT NOT NULL,
    notified_at  TEXT
);

CREATE INDEX outages_start_at ON outages(start_at);
CREATE INDEX outages_post_id ON outages(post_id);
CREATE INDEX outage_changes_notified_at ON outage_changes(notified_at);
