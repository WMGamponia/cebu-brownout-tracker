-- Phase 3 (spec 5.1): Telegram subscribers, subscriptions, the per-subscriber alert ledger and
-- admin notes. Bookkeeping times: UTC ISO 8601. The getUpdates offset lives in `meta`.

CREATE TABLE subscribers (
    id            INTEGER PRIMARY KEY,
    chat_id       INTEGER NOT NULL UNIQUE,
    display_name  TEXT,
    created_at    TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE subscriptions (
    subscriber_id  INTEGER NOT NULL REFERENCES subscribers(id),
    psgc_code      TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (subscriber_id, psgc_code)
);
CREATE TABLE alerts_sent (
    id             INTEGER PRIMARY KEY,
    subscriber_id  INTEGER NOT NULL REFERENCES subscribers(id),
    outage_id      INTEGER NOT NULL REFERENCES outages(id),
    kind           TEXT NOT NULL CHECK (kind IN ('day_before', 'day_of', 'change')),
    change_id      INTEGER REFERENCES outage_changes(id),
    sent_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX alerts_sent_item
    ON alerts_sent(subscriber_id, outage_id, kind, coalesce(change_id, 0));
CREATE TABLE admin_notes_sent (
    key      TEXT PRIMARY KEY,   -- e.g. 'parse_failed:<post_id>:<version_id>'
    sent_at  TEXT NOT NULL
);
