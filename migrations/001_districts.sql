-- Migration: Create districts table for Moscow okrugs and districts boundaries
--
-- This migration stores simplified geometries (as GeoJSON) alongside hierarchy
-- and bounding boxes. Coverage statistics (H3-based) will be filled later.

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS districts (
    id INTEGER PRIMARY KEY,
    level TEXT CHECK(level IN ('okrug', 'district')) NOT NULL,
    name_ru TEXT NOT NULL,
    parent_id INTEGER,
    geom_geojson TEXT NOT NULL,
    bbox_min_lon REAL,
    bbox_min_lat REAL,
    bbox_max_lon REAL,
    bbox_max_lat REAL,
    total_cells INTEGER DEFAULT 0,
    total_weight REAL DEFAULT 0.0,
    FOREIGN KEY (parent_id) REFERENCES districts(id)
);

CREATE INDEX IF NOT EXISTS idx_districts_level ON districts(level);
CREATE INDEX IF NOT EXISTS idx_districts_parent ON districts(parent_id);

COMMIT;

