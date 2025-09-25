-- Migration: district_cells coverage table

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS district_cells (
    district_id INTEGER NOT NULL,
    h3 TEXT NOT NULL,
    coverage REAL NOT NULL,
    PRIMARY KEY (district_id, h3),
    FOREIGN KEY (district_id) REFERENCES districts(id)
);

CREATE INDEX IF NOT EXISTS idx_dc_h3 ON district_cells(h3);

COMMIT;


