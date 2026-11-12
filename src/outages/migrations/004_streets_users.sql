-- Phase 5 (spec 4.1): street matches against the local OSM extract, and the approve-to-join
-- status of subscribers. Existing subscribers become 'approved' through the default.

CREATE TABLE street_matches (
    areas_hash    TEXT NOT NULL,
    seq           INTEGER NOT NULL,          -- area_entities.seq
    status        TEXT NOT NULL,             -- matched | alias | not_found | no_osm_data
    osm_name      TEXT,                      -- OSM name that matched
    geometry_json TEXT,                      -- MultiLineString coordinates, 5 decimals; NULL unless drawn
    osm_date      TEXT,                      -- manifest fetched_at of the extract used
    PRIMARY KEY (areas_hash, seq)
);
ALTER TABLE subscribers ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'; -- pending | approved | blocked
ALTER TABLE subscribers ADD COLUMN requested_at TEXT;
ALTER TABLE subscribers ADD COLUMN decided_at TEXT;
