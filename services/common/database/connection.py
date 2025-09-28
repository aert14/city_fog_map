import os
from typing import Optional

import psycopg2

BASE_VISIT_RESOLUTION = 10
PRIMARY_COVERAGE_THRESHOLD = 0.5

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

_CONNECTION: Optional[psycopg2.extensions.connection] = None


def get_connection() -> psycopg2.extensions.connection:
    global _CONNECTION
    if _CONNECTION is None or _CONNECTION.closed:
        _CONNECTION = psycopg2.connect(DATABASE_URL)

    if _CONNECTION and not _CONNECTION.closed:
        try:
            with _CONNECTION.cursor() as test_cur:
                test_cur.execute("SELECT 1")
        except psycopg2.Error:
            try:
                _CONNECTION.rollback()
            except Exception:
                _CONNECTION.close()
                _CONNECTION = psycopg2.connect(DATABASE_URL)

    return _CONNECTION


def init_db(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL UNIQUE,
                username TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS districts (
                id INTEGER PRIMARY KEY,
                level TEXT CHECK(level IN ('okrug', 'district')) NOT NULL,
                name_ru TEXT NOT NULL,
                parent_id INTEGER,
                geom GEOMETRY(Geometry, 4326) NOT NULL,
                geom_geojson TEXT,
                bbox_min_lon REAL,
                bbox_min_lat REAL,
                bbox_max_lon REAL,
                bbox_max_lat REAL,
                total_cells INTEGER DEFAULT 0,
                total_weight REAL DEFAULT 0.0,
                FOREIGN KEY (parent_id) REFERENCES districts(id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_districts_geom ON districts USING GIST(geom);")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS district_cells (
                district_id INTEGER NOT NULL,
                h3 TEXT NOT NULL,
                coverage REAL NOT NULL,
                PRIMARY KEY (district_id, h3),
                FOREIGN KEY (district_id) REFERENCES districts(id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_districts_level ON districts(level);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_districts_parent ON districts(parent_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_district_cells_h3 ON district_cells(h3);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS circles (
                user_id INTEGER NOT NULL,
                geokey TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, geokey),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                h3_resolution INTEGER NOT NULL DEFAULT 11,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_visits_atomic (
                user_id INTEGER NOT NULL,
                h3 TEXT NOT NULL,
                ts BIGINT NOT NULL,
                PRIMARY KEY (user_id, h3),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_district_stats (
                user_id INTEGER NOT NULL,
                district_id INTEGER NOT NULL,
                visited_cells INTEGER NOT NULL DEFAULT 0,
                visited_weight REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, district_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (district_id) REFERENCES districts(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_okrug_stats (
                user_id INTEGER NOT NULL,
                okrug_id INTEGER NOT NULL,
                visited_cells INTEGER NOT NULL DEFAULT 0,
                visited_weight REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, okrug_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (okrug_id) REFERENCES districts(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS achievements (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                icon TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id INTEGER NOT NULL,
                achievement_id INTEGER NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, achievement_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (achievement_id) REFERENCES achievements(id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_visits_atomic_h3 ON user_visits_atomic(h3);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_visits_atomic_user ON user_visits_atomic(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_district_stats_user ON user_district_stats(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_okrug_stats_user ON user_okrug_stats(user_id);")

        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name = 'user_visits_atomic'
                               AND column_name = 'geom') THEN
                    ALTER TABLE user_visits_atomic ADD COLUMN geom GEOMETRY(Point, 4326);
                END IF;
            END $$;
        """
        )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_visits_atomic_geom ON user_visits_atomic USING GIST(geom);")

        cur.execute(
            """
            INSERT INTO achievements (code, name, description, icon) VALUES
            ('FIRST_STEP', 'Первый шаг', 'Исследовать свою первую территорию.', 'footprints'),
            ('EXPLORER_100', 'Исследователь', 'Исследовать 100 территорий.', 'compass'),
            ('CARTOGRAPHER_1000', 'Картограф', 'Исследовать 1000 территорий.', 'map')
            ON CONFLICT (code) DO NOTHING;
            """
        )

    conn.commit()

