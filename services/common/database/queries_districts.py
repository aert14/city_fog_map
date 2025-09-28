from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
import psycopg2.extras


def select_district_for_cell(
    conn: psycopg2.extensions.connection,
    h3_index: str,
) -> Optional[Tuple[int, float]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT district_id, coverage
            FROM district_cells
            WHERE h3 = %s
            ORDER BY coverage DESC
            LIMIT 1
            """,
            (h3_index,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return int(row[0]), float(row[1])


def select_district_parent(
    conn: psycopg2.extensions.connection,
    district_id: int,
) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT parent_id FROM districts WHERE id = %s",
            (district_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        parent = row[0]
        return int(parent) if parent is not None else None


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
        else:
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


def fetch_districts_by_ids(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    district_ids: Iterable[int],
) -> List[Dict[str, Any]]:
    ids_list = list(district_ids)
    if not ids_list:
        return []

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        sql = """
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
                d.total_cells,
                d.total_weight,
                COALESCE(uds.visited_cells, 0) AS user_visited_cells,
                COALESCE(uds.visited_weight, 0.0) AS user_visited_weight
            FROM districts AS d
            LEFT JOIN user_district_stats AS uds
                ON uds.district_id = d.id AND uds.user_id = %s
            WHERE d.id IN %s
        """
        params = [user_id, tuple(ids_list)]
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def get_district_by_id(
    conn: psycopg2.extensions.connection,
    district_id: int,
) -> Optional[Dict[str, Any]]:
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
    conn: psycopg2.extensions.connection,
    district_id: int,
) -> List[Tuple[str, float]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h3, coverage FROM district_cells WHERE district_id = %s",
            (district_id,),
        )
        return [(str(row[0]), float(row[1])) for row in cur.fetchall()]


def fetch_all_districts_with_user_progress(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        district_sql = """
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
                d.total_cells,
                d.total_weight,
                COALESCE(s.visited_cells, 0) AS user_visited_cells,
                COALESCE(s.visited_weight, 0.0) AS user_visited_weight
            FROM districts AS d
            LEFT JOIN user_district_stats AS s ON s.district_id = d.id AND s.user_id = %s
            WHERE d.level = 'district'
            ORDER BY d.name_ru
        """

        okrug_sql = """
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
                COALESCE(child_totals.total_cells, d.total_cells, 0) AS total_cells,
                COALESCE(child_totals.total_weight, d.total_weight, 0.0) AS total_weight,
                COALESCE(s.visited_cells, 0) AS user_visited_cells,
                COALESCE(s.visited_weight, 0.0) AS user_visited_weight
            FROM districts AS d
            LEFT JOIN (
                SELECT parent_id AS okrug_id,
                       SUM(total_cells) AS total_cells,
                       SUM(total_weight) AS total_weight
                FROM districts
                WHERE level = 'district'
                GROUP BY parent_id
            ) AS child_totals ON child_totals.okrug_id = d.id
            LEFT JOIN (
                SELECT
                    child.parent_id AS okrug_id,
                    COALESCE(SUM(uds.visited_cells), 0) AS visited_cells,
                    COALESCE(SUM(uds.visited_weight), 0.0) AS visited_weight
                FROM districts AS child
                LEFT JOIN user_district_stats AS uds
                    ON uds.district_id = child.id AND uds.user_id = %s
                WHERE child.level = 'district' AND child.parent_id IS NOT NULL
                GROUP BY child.parent_id
            ) AS s ON s.okrug_id = d.id
            WHERE d.level = 'okrug'
            ORDER BY d.name_ru
        """

        results: List[Dict[str, Any]] = []
        cur.execute(district_sql, (user_id,))
        results.extend([dict(row) for row in cur.fetchall()])

        cur.execute(okrug_sql, (user_id,))
        results.extend([dict(row) for row in cur.fetchall()])

        return results


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

