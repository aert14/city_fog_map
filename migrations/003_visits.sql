CREATE TABLE IF NOT EXISTS user_visits_atomic (
    user_id INTEGER NOT NULL,
    h3 TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (user_id, h3),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_district_stats (
    user_id INTEGER NOT NULL,
    district_id INTEGER NOT NULL,
    visited_cells INTEGER NOT NULL DEFAULT 0,
    visited_weight REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (user_id, district_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (district_id) REFERENCES districts(id)
);

CREATE TABLE IF NOT EXISTS user_okrug_stats (
    user_id INTEGER NOT NULL,
    okrug_id INTEGER NOT NULL,
    visited_cells INTEGER NOT NULL DEFAULT 0,
    visited_weight REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (user_id, okrug_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (okrug_id) REFERENCES districts(id)
);
