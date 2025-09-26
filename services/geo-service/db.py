import os
import psycopg2
import psycopg2.extras
from typing import Any, Dict, Iterable, List, Optional, Tuple

import h3

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

BASE_VISIT_RESOLUTION = 9
PRIMARY_COVERAGE_THRESHOLD = 0.5

_CONNECTION: Optional[psycopg2.extensions.connection] = None


def get_connection() -> psycopg2.extensions.connection:
    global _CONNECTION
    if _CONNECTION is None or _CONNECTION.closed:
        _CONNECTION = psycopg2.connect(DATABASE_URL)

    # Check if connection is in a failed transaction state
    if _CONNECTION and not _CONNECTION.closed:
        try:
            # Test the connection and rollback any failed transaction
            with _CONNECTION.cursor() as test_cur:
                test_cur.execute("SELECT 1")
        except psycopg2.Error:
            # If there's an error, rollback and try to recover
            try:
                _CONNECTION.rollback()
            except:
                # If rollback fails, close and reconnect
                _CONNECTION.close()
                _CONNECTION = psycopg2.connect(DATABASE_URL)

    return _CONNECTION


def ensure_user(conn: psycopg2.extensions.connection, tg_id: int, username: Optional[str]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE tg_id = %s", (tg_id,))
        row = cur.fetchone()
        if row:
            user_id = int(row[0])
            if username:
                cur.execute("UPDATE users SET username = %s WHERE id = %s", (username, user_id))
                conn.commit()
            return user_id

        cur.execute(
            "INSERT INTO users (tg_id, username) VALUES (%s, %s) RETURNING id",
            (tg_id, username),
        )
        user_id = cur.fetchone()[0]
        cur.execute("INSERT INTO user_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
        conn.commit()
        return user_id


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
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                h3_resolution INTEGER NOT NULL DEFAULT 11,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
    conn.commit()


def count_user_visited_hexes(conn: psycopg2.extensions.connection, user_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM user_visits_atomic WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0


def select_user_hexes(conn: psycopg2.extensions.connection, user_id: int) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h3 FROM user_visits_atomic WHERE user_id = %s",
            (user_id,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def aggregate_hexagons(all_user_hexes: List[str]) -> List[str]:
    """Aggregate hexagons to parent indices for network optimization.

    Groups child hexagons into parent hexagons if all children of a parent are present.
    Returns a mixed set of hexagons at different resolutions.
    """
    if not all_user_hexes:
        return []

    resolution_to_group_at = BASE_VISIT_RESOLUTION - 2
    parents = {}

    for hex_id in all_user_hexes:
        parent = h3.cell_to_parent(hex_id, resolution_to_group_at)
        if parent not in parents:
            parents[parent] = set()
        parents[parent].add(hex_id)

    final_hexes = set()
    for parent, children in parents.items():
        # Check if all children of this parent are present
        expected_children_count = h3.cell_to_children_size(parent, BASE_VISIT_RESOLUTION)
        if len(children) == expected_children_count:
            # All children are present, add parent instead
            final_hexes.add(parent)
        else:
            # Not all children, add them individually
            final_hexes.update(children)

    return list(final_hexes)


def select_user_hexes_in_bbox(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> List[str]:
    """Select user hexagons within a bounding box using spatial query for efficiency."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT h3 FROM user_visits_atomic
            WHERE user_id = %s
              AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
            """,
            (user_id, min_lon, min_lat, max_lon, max_lat),
        )
        hexagons = [str(row[0]) for row in cur.fetchall()]

    # Aggregate hexagons to parent indices for network optimization
    return aggregate_hexagons(hexagons)


def fetch_districts_in_bbox(
    conn: psycopg2.extensions.connection,
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

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        stats_join_params = [user_id]
        if level == "district":
            stats_join = "LEFT JOIN user_district_stats AS s ON s.district_id = d.id AND s.user_id = %s"
            total_cells_expr = "d.total_cells"
            total_weight_expr = "d.total_weight"
            additional_join = ""
        else: # okrug
            total_cells_expr = "COALESCE(child_totals.total_cells, d.total_cells, 0)"
            total_weight_expr = "COALESCE(child_totals.total_weight, d.total_weight, 0.0)"
            additional_join = (
                "LEFT JOIN (\n"
                "    SELECT parent_id AS okrug_id,\n"
                "           SUM(total_cells) AS total_cells,\n"
                "           SUM(total_weight) AS total_weight\n"
                "    FROM districts\n"
                "    WHERE level = 'district'\n"
                "    GROUP BY parent_id\n"
                ") AS child_totals ON child_totals.okrug_id = d.id"
            )
            stats_join = (
                "LEFT JOIN (\n"
                "    SELECT\n"
                "        child.parent_id AS okrug_id,\n"
                "        COALESCE(SUM(uds.visited_cells), 0) AS visited_cells,\n"
                "        COALESCE(SUM(uds.visited_weight), 0.0) AS visited_weight\n"
                "    FROM districts AS child\n"
                "    LEFT JOIN user_district_stats AS uds\n"
                "        ON uds.district_id = child.id AND uds.user_id = %s\n"
                "    WHERE child.level = 'district' AND child.parent_id IS NOT NULL\n"
                "    GROUP BY child.parent_id\n"
                ") AS s ON s.okrug_id = d.id"
            )

        sql = f"""
            SELECT
                d.id,
                d.level,
                d.name_ru,
                d.parent_id,
                ST_AsGeoJSON(d.geom) as geom_geojson,
                d.bbox_min_lon,
                d.bbox_min_lat,
                d.bbox_max_lon,
                d.bbox_max_lat,
                {total_cells_expr} AS total_cells,
                {total_weight_expr} AS total_weight,
                COALESCE(s.visited_cells, 0) AS user_visited_cells,
                COALESCE(s.visited_weight, 0.0) AS user_visited_weight
            FROM districts AS d
            {additional_join}
            {stats_join}
            WHERE d.level = %s
              AND d.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
            ORDER BY d.name_ru
        """

        params = stats_join_params + [level, min_lon, min_lat, max_lon, max_lat]
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def get_district_by_id(conn: psycopg2.extensions.connection, district_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT
                id,
                level,
                name_ru,
                parent_id,
                ST_AsGeoJSON(geom) as geom_geojson,
                bbox_min_lon,
                bbox_min_lat,
                bbox_max_lon,
                bbox_max_lat,
                total_cells,
                total_weight
            FROM districts
            WHERE id = %s
            """,
            (district_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_district_cells(
    conn: psycopg2.extensions.connection, district_id: int
) -> List[Tuple[str, float]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h3, coverage FROM district_cells WHERE district_id = %s",
            (district_id,),
        )
        return [(str(row[0]), float(row[1])) for row in cur.fetchall()]


def fetch_user_visited_cells_for_district(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    district_id: int,
) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.h3
            FROM user_visits_atomic AS v
            INNER JOIN district_cells AS dc ON dc.h3 = v.h3
            WHERE v.user_id = %s AND dc.district_id = %s
            """,
            (user_id, district_id),
        )
        return [str(row[0]) for row in cur.fetchall()]


def fetch_user_total_progress(conn: psycopg2.extensions.connection, user_id: int) -> Dict[str, float]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT
                COALESCE(SUM(d.total_cells), 0) AS total_cells,
                COALESCE(SUM(d.total_weight), 0.0) AS total_weight,
                COALESCE(SUM(uds.visited_cells), 0) AS visited_cells,
                COALESCE(SUM(uds.visited_weight), 0.0) AS visited_weight
            FROM districts AS d
            LEFT JOIN user_district_stats AS uds
                ON uds.district_id = d.id AND uds.user_id = %s
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
            "total_cells": int(row["total_cells"] or 0),
            "total_weight": float(row["total_weight"] or 0.0),
            "visited_cells": int(row["visited_cells"] or 0),
            "visited_weight": float(row["visited_weight"] or 0.0),
        }


def fetch_user_okrug_progress(conn: psycopg2.extensions.connection, user_id: int) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
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
                ON uds.district_id = d.id AND uds.user_id = %s
            WHERE o.level = 'okrug'
            GROUP BY o.id, o.name_ru, o.parent_id
            ORDER BY o.name_ru
        """,
            (user_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_user_bottom_districts(
    conn: psycopg2.extensions.connection, user_id: int, limit: int = 3
) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
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
                ON uds.district_id = d.id AND uds.user_id = %s
            WHERE d.level = 'district'
              AND d.total_cells > 0
            ORDER BY progress_ratio ASC, d.name_ru ASC
            LIMIT %s
        """,
            (user_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def get_total_cells_and_weight(
    conn: psycopg2.extensions.connection, *, level: str
) -> Tuple[int, float]:
    if level not in {"district", "okrug"}:
        raise ValueError("Unsupported level")

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if level == "district":
            sql = """
                SELECT
                    COALESCE(SUM(total_cells), 0) AS total_cells,
                    COALESCE(SUM(total_weight), 0.0) AS total_weight
                FROM districts
                WHERE level = 'district'
            """
            cur.execute(sql)
            row = cur.fetchone()
            if not row:
                return 0, 0.0
            return int(row["total_cells"] or 0), float(row["total_weight"] or 0.0)

        sql = """
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
        cur.execute(sql)
        row = cur.fetchone()
        if not row:
            return 0, 0.0
        return int(row["total_cells"] or 0), float(row["total_weight"] or 0.0)


def fetch_leaderboard(
    conn: psycopg2.extensions.connection,
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

    import time
    now_ts = int(time.time())
    period_seconds = 7 * 24 * 3600 if period == "week" else 90 * 24 * 3600
    since_ts = max(0, now_ts - period_seconds)

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        sql = """
            WITH recent_visits AS (
                SELECT
                    v.user_id,
                    dc.coverage AS coverage,
                    CASE WHEN dc.coverage >= %s THEN 1 ELSE 0 END AS cell_credit
                FROM user_visits_atomic AS v
                INNER JOIN district_cells AS dc ON dc.h3 = v.h3
                INNER JOIN districts AS child ON child.id = dc.district_id
                LEFT JOIN districts AS parent ON parent.id = child.parent_id
                WHERE v.ts >= %s
                  AND (%s = 'district' OR parent.id IS NOT NULL)
            ),
            aggregated AS (
                SELECT
                    rv.user_id,
                    SUM(rv.cell_credit) AS visited_cells,
                    SUM(rv.coverage) AS visited_weight
                FROM recent_visits AS rv
                GROUP BY rv.user_id
            )
            SELECT
                a.user_id,
                u.username,
                COALESCE(a.visited_cells, 0) AS visited_cells,
                COALESCE(a.visited_weight, 0.0) AS visited_weight
            FROM aggregated AS a
            INNER JOIN users AS u ON u.id = a.user_id
            WHERE a.visited_cells > 0 OR a.visited_weight > 0.0
            ORDER BY a.visited_cells DESC, a.visited_weight DESC, a.user_id ASC
            LIMIT %s
        """

        params = [PRIMARY_COVERAGE_THRESHOLD, since_ts, level, limit]
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
