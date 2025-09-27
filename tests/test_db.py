import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import h3

# Test database using SQLite
BASE_VISIT_RESOLUTION = 10
PRIMARY_COVERAGE_THRESHOLD = 0.5

_CONNECTION: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    global _CONNECTION
    if _CONNECTION is None:
        db_path = os.getenv("DB_PATH")
        if not db_path:
            raise ValueError("DB_PATH environment variable is not set")
        _CONNECTION = sqlite3.connect(db_path)
        _CONNECTION.row_factory = sqlite3.Row
    return _CONNECTION


def init_db(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS circles (
                user_id INTEGER NOT NULL,
                geokey TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, geokey),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                h3_resolution INTEGER NOT NULL DEFAULT 9,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

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
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_okrug_stats (
                user_id INTEGER NOT NULL,
                okrug_id INTEGER NOT NULL,
                visited_cells INTEGER NOT NULL DEFAULT 0,
                visited_weight REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, okrug_id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS districts (
                id INTEGER PRIMARY KEY,
                level TEXT CHECK(level IN ('okrug', 'district')) NOT NULL,
                name_ru TEXT NOT NULL,
                parent_id INTEGER,
                geom GEOMETRY,
                geom_geojson TEXT,
                bbox_min_lon REAL,
                bbox_min_lat REAL,
                bbox_max_lon REAL,
                bbox_max_lat REAL,
                total_cells INTEGER DEFAULT 0,
                total_weight REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS district_cells (
                district_id INTEGER NOT NULL,
                h3 TEXT NOT NULL,
                coverage REAL NOT NULL,
                PRIMARY KEY (district_id, h3),
                FOREIGN KEY (district_id) REFERENCES districts(id)
            );

            CREATE INDEX IF NOT EXISTS idx_user_visits_atomic_h3 ON user_visits_atomic(h3);
            CREATE INDEX IF NOT EXISTS idx_user_visits_atomic_user ON user_visits_atomic(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_district_stats_user ON user_district_stats(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_okrug_stats_user ON user_okrug_stats(user_id);
            """
        )


def ensure_user(conn: sqlite3.Connection, tg_id: int, username: Optional[str]) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if row:
            user_id = int(row[0])
            if username:
                cur.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
            return user_id

        cur.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (tg_id, username),
        )
        user_id = cur.lastrowid
        cur.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        return user_id


def insert_circle_if_new(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    geokey: str,
    lat: float,
    lon: float,
) -> bool:
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO circles (user_id, geokey, lat, lon)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, geokey, float(lat), float(lon)),
        )
        return cur.rowcount > 0


def count_circles(conn: sqlite3.Connection, *, user_id: int) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM circles WHERE user_id = ?", (user_id,))
    return int(cur.fetchone()[0])


def select_circles_in_bbox(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> List[Tuple[float, float, str]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT lat, lon, geokey
        FROM circles
        WHERE user_id = ?
          AND lat BETWEEN ? AND ?
          AND lon BETWEEN ? AND ?
        ORDER BY created_at DESC
        LIMIT 10000
        """,
        (user_id, min_lat, max_lat, min_lon, max_lon),
    )
    return [(float(r[0]), float(r[1]), str(r[2])) for r in cur.fetchall()]


def delete_circle_by_geokey(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    geokey: str,
) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM circles WHERE user_id = ? AND geokey = ?",
            (user_id, geokey),
        )
        return cur.rowcount


def update_user_h3_resolution(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    h3_resolution: int,
) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO user_settings (user_id, h3_resolution)
            VALUES (?, ?)
            """,
            (user_id, h3_resolution),
        )
        return cur.rowcount


def get_user_h3_resolution(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT h3_resolution FROM user_settings WHERE user_id = ?", (user_id,)
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    return BASE_VISIT_RESOLUTION


def clear_user_circles(conn: sqlite3.Connection, user_id: int) -> int:
    with conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM circles WHERE user_id = ?", (user_id,))
        return cur.rowcount


def clear_all(conn: sqlite3.Connection) -> tuple[int, int]:
    with conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM circles")
        count_circles = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM users")
        count_users = int(cur.fetchone()[0])

        cur.execute("DELETE FROM circles")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM user_settings")
        cur.execute("DELETE FROM user_visits_atomic")
        cur.execute("DELETE FROM user_district_stats")
        cur.execute("DELETE FROM user_okrug_stats")
        return count_circles, count_users


def count_user_visited_hexes(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM user_visits_atomic WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def select_district_for_cell(conn: sqlite3.Connection, h3_index: str) -> Optional[Tuple[int, float]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT district_id, coverage
        FROM district_cells
        WHERE h3 = ?
        ORDER BY coverage DESC
        LIMIT 1
        """,
        (h3_index,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return int(row[0]), float(row[1])


def select_district_parent(conn: sqlite3.Connection, district_id: int) -> Optional[int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT parent_id FROM districts WHERE id = ?",
        (district_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    parent = row[0]
    return int(parent) if parent is not None else None


def record_visit_and_increment_stats(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    h3_index: str,
    district_id: int,
    coverage: float,
    okrug_id: Optional[int],
    now_ts: Optional[int] = None,
) -> bool:
    ts = int(now_ts if now_ts is not None else time.time())
    with conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO user_visits_atomic(user_id, h3, ts)
            VALUES (?, ?, ?)
            """,
            (user_id, h3_index, ts),
        )
        added = cur.rowcount > 0
        if not added:
            return False

        increment_cell = 1 if coverage >= PRIMARY_COVERAGE_THRESHOLD else 0

        _update_statistic(
            cur,
            table="user_district_stats",
            key_field="district_id",
            user_id=user_id,
            region_id=district_id,
            increment_cell=increment_cell,
            coverage=coverage,
        )

        if okrug_id is not None:
            _update_statistic(
                cur,
                table="user_okrug_stats",
                key_field="okrug_id",
                user_id=user_id,
                region_id=okrug_id,
                increment_cell=increment_cell,
                coverage=coverage,
            )

    return True


def _update_statistic(
    cur: sqlite3.Cursor,
    *,
    table: str,
    key_field: str,
    user_id: int,
    region_id: int,
    increment_cell: int,
    coverage: float,
) -> None:
    cur.execute(
        f"""
        INSERT OR REPLACE INTO {table} (user_id, {key_field}, visited_cells, visited_weight)
        VALUES (
            ?,
            ?,
            COALESCE((SELECT visited_cells FROM {table} WHERE user_id = ? AND {key_field} = ?), 0) + ?,
            COALESCE((SELECT visited_weight FROM {table} WHERE user_id = ? AND {key_field} = ?), 0.0) + ?
        )
        """,
        (user_id, region_id, user_id, region_id, increment_cell, user_id, region_id, coverage),
    )


def fetch_user_stats(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    district_id: Optional[int],
    okrug_id: Optional[int],
) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "total_circles": count_user_visited_hexes(conn, user_id),
        "district": None,
        "okrug": None,
    }
    cur = conn.cursor()
    if district_id is not None:
        cur.execute(
            """
            SELECT visited_cells, visited_weight
            FROM user_district_stats
            WHERE user_id = ? AND district_id = ?
            """,
            (user_id, district_id),
        )
        row = cur.fetchone()
        if row:
            stats["district"] = {
                "id": district_id,
                "visited_cells": int(row[0]),
                "visited_weight": float(row[1]),
            }
    if okrug_id is not None:
        cur.execute(
            """
            SELECT visited_cells, visited_weight
            FROM user_okrug_stats
            WHERE user_id = ? AND okrug_id = ?
            """,
            (user_id, okrug_id),
        )
        row = cur.fetchone()
        if row:
            stats["okrug"] = {
                "id": okrug_id,
                "visited_cells": int(row[0]),
                "visited_weight": float(row[1]),
            }
    return stats


def select_user_hexes(conn: sqlite3.Connection, user_id: int) -> List[str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT h3 FROM user_visits_atomic WHERE user_id = ?",
        (user_id,),
    )
    return [str(row[0]) for row in cur.fetchall()]


def fetch_districts_in_bbox(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    level: str,
) -> List[Dict[str, Any]]:
    if level not in {"district", "okrug"}:
        raise ValueError("Unsupported level")

    cur = conn.cursor()
    # Simplified query for testing - ignoring spatial complexity
    cur.execute(
        """
        SELECT
            id, level, name_ru, parent_id,
            geom_geojson,
            bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
            total_cells, total_weight,
            0 as user_visited_cells, 0.0 as user_visited_weight
        FROM districts
        WHERE level = ?
        ORDER BY name_ru
        """,
        (level,),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_districts_by_ids(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    district_ids: Iterable[int],
) -> List[Dict[str, Any]]:
    ids_list = list(district_ids)
    if not ids_list:
        return []

    placeholders = ','.join('?' * len(ids_list))
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT
            id, level, name_ru, parent_id,
            geom_geojson,
            bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
            total_cells, total_weight,
            0 as user_visited_cells, 0.0 as user_visited_weight
        FROM districts
        WHERE id IN ({placeholders})
        """,
        ids_list,
    )
    return [dict(row) for row in cur.fetchall()]


def select_user_hexes_in_bbox(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> List[str]:
    # Simplified implementation for testing
    hexagons: List[str] = []
    for h3_index in select_user_hexes(conn, user_id):
        lat, lon = h3.cell_to_latlng(h3_index)
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            hexagons.append(h3_index)
    return hexagons


def delete_visit_by_hex(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    h3_index: str,
) -> int:
    with conn:
        cur = conn.cursor()
        district_info = select_district_for_cell(conn, h3_index)
        okrug_id: Optional[int] = None
        coverage = 0.0
        decrement_cell = 0
        if district_info:
            district_id, coverage = district_info
            okrug_id = select_district_parent(conn, district_id)
            if coverage >= PRIMARY_COVERAGE_THRESHOLD:
                decrement_cell = 1

        cur.execute(
            "DELETE FROM user_visits_atomic WHERE user_id = ? AND h3 = ?",
            (user_id, h3_index),
        )
        if cur.rowcount == 0:
            return 0

        if district_info:
            district_id, _ = district_info
            cur.execute(
                """
                UPDATE user_district_stats
                SET visited_cells = MAX(0, visited_cells - ?),
                    visited_weight = MAX(0.0, visited_weight - ?)
                WHERE user_id = ? AND district_id = ?
                """,
                (decrement_cell, coverage, user_id, district_id),
            )
            if okrug_id is not None:
                cur.execute(
                    """
                    UPDATE user_okrug_stats
                    SET visited_cells = MAX(0, visited_cells - ?),
                        visited_weight = MAX(0.0, visited_weight - ?)
                    WHERE user_id = ? AND okrug_id = ?
                    """,
                    (decrement_cell, coverage, user_id, okrug_id),
                )
    return 1


def get_district_by_id(conn: sqlite3.Connection, district_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id, level, name_ru, parent_id,
            geom_geojson,
            bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
            total_cells, total_weight
        FROM districts
        WHERE id = ?
        """,
        (district_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_district_cells(
    conn: sqlite3.Connection, district_id: int
) -> List[Tuple[str, float]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT h3, coverage FROM district_cells WHERE district_id = ?",
        (district_id,),
    )
    return [(str(row[0]), float(row[1])) for row in cur.fetchall()]


def fetch_user_visited_cells_for_district(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    district_id: int,
) -> List[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT v.h3
        FROM user_visits_atomic AS v
        INNER JOIN district_cells AS dc ON dc.h3 = v.h3
        WHERE v.user_id = ? AND dc.district_id = ?
        """,
        (user_id, district_id),
    )
    return [str(row[0]) for row in cur.fetchall()]


def fetch_user_total_progress(conn: sqlite3.Connection, user_id: int) -> Dict[str, float]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COALESCE(SUM(d.total_cells), 0) AS total_cells,
            COALESCE(SUM(d.total_weight), 0.0) AS total_weight,
            COALESCE(SUM(uds.visited_cells), 0) AS visited_cells,
            COALESCE(SUM(uds.visited_weight), 0.0) AS visited_weight
        FROM districts AS d
        LEFT JOIN user_district_stats AS uds
            ON uds.district_id = d.id AND uds.user_id = ?
        WHERE d.level = 'district'
        """,
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return {
            "total_cells": 0,
            "total_weight": 0.0,
            "visited_cells": 0,
            "visited_weight": 0.0,
        }
    return {
        "total_cells": int(row[0] or 0),
        "total_weight": float(row[1] or 0.0),
        "visited_cells": int(row[2] or 0),
        "visited_weight": float(row[3] or 0.0),
    }


def fetch_user_okrug_progress(conn: sqlite3.Connection, user_id: int) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            o.id,
            o.name_ru,
            o.parent_id,
            COALESCE(SUM(d.total_cells), 0) AS total_cells,
            COALESCE(SUM(d.total_weight), 0.0) AS total_weight,
            COALESCE(SUM(uds.visited_cells), 0) AS visited_cells,
            COALESCE(SUM(uds.visited_weight), 0.0) AS visited_weight
        FROM districts AS o
        LEFT JOIN districts AS d ON d.parent_id = o.id AND d.level = 'district'
        LEFT JOIN user_district_stats AS uds
            ON uds.district_id = d.id AND uds.user_id = ?
        WHERE o.level = 'okrug'
        GROUP BY o.id, o.name_ru, o.parent_id
        ORDER BY o.name_ru
        """,
        (user_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_user_bottom_districts(
    conn: sqlite3.Connection, user_id: int, limit: int = 3
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            d.id,
            d.name_ru,
            d.parent_id,
            parent.name_ru AS parent_name,
            d.total_cells,
            d.total_weight,
            COALESCE(uds.visited_cells, 0) AS visited_cells,
            COALESCE(uds.visited_weight, 0.0) AS visited_weight,
            CASE
                WHEN d.total_cells > 0 THEN
                    CAST(COALESCE(uds.visited_cells, 0) AS REAL) / d.total_cells
                ELSE NULL
            END AS progress_ratio
        FROM districts AS d
        LEFT JOIN districts AS parent ON parent.id = d.parent_id
        LEFT JOIN user_district_stats AS uds
            ON uds.district_id = d.id AND uds.user_id = ?
        WHERE d.level = 'district'
          AND d.total_cells > 0
        ORDER BY progress_ratio ASC, d.name_ru ASC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(row) for row in cur.fetchall()]


def get_total_cells_and_weight(
    conn: sqlite3.Connection, *, level: str
) -> Tuple[int, float]:
    if level not in {"district", "okrug"}:
        raise ValueError("Unsupported level")

    cur = conn.cursor()
    if level == "district":
        cur.execute(
            """
            SELECT
                COALESCE(SUM(total_cells), 0) AS total_cells,
                COALESCE(SUM(total_weight), 0.0) AS total_weight
            FROM districts
            WHERE level = 'district'
            """
        )
    else:
        cur.execute(
            """
            SELECT
                COALESCE(SUM(grouped.total_cells), 0) AS total_cells,
                COALESCE(SUM(grouped.total_weight), 0.0) AS total_weight
            FROM (
                SELECT
                    ok.id,
                    COALESCE(child_totals.total_cells, ok.total_cells, 0) AS total_cells,
                    COALESCE(child_totals.total_weight, ok.total_weight, 0.0) AS total_weight
                FROM districts AS ok
                LEFT JOIN (
                    SELECT
                        parent_id AS okrug_id,
                        SUM(total_cells) AS total_cells,
                        SUM(total_weight) AS total_weight
                    FROM districts
                    WHERE level = 'district'
                    GROUP BY parent_id
                ) AS child_totals ON child_totals.okrug_id = ok.id
                WHERE ok.level = 'okrug'
            ) AS grouped
            """
        )

    row = cur.fetchone()
    if not row:
        return 0, 0.0
    return int(row[0] or 0), float(row[1] or 0.0)


def fetch_leaderboard(
    conn: sqlite3.Connection,
    *,
    level: str,
    period: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if level not in {"district", "okrug"}:
        raise ValueError("Unsupported level")
    if period not in {"week", "season"}:
        raise ValueError("Unsupported period")
    if limit <= 0:
        return []

    # Simplified implementation for testing - just return empty list
    # Real implementation would be complex with time-based filtering
    return []
