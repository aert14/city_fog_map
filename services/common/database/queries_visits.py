from __future__ import annotations

import logging
import time
from typing import List, Optional

import h3
import psycopg2

from .connection import PRIMARY_COVERAGE_THRESHOLD
from .queries_districts import select_district_for_cell, select_district_parent


logger = logging.getLogger(__name__)


def record_atomic_visit(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    h3_index: str,
    now_ts: Optional[int] = None,
) -> bool:
    ts = int(now_ts if now_ts is not None else time.time())
    lat, lon = h3.cell_to_latlng(h3_index)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_visits_atomic(user_id, h3, ts, geom)
            VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ON CONFLICT DO NOTHING
            """,
            (user_id, h3_index, ts, lon, lat),
        )
        added = cur.rowcount > 0
        if not added:
            conn.rollback()
            return False

    conn.commit()
    return True


def record_visit_and_increment_stats(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    h3_index: str,
    district_id: int,
    coverage: float,
    okrug_id: Optional[int],
    now_ts: Optional[int] = None,
) -> bool:
    ts = int(now_ts if now_ts is not None else time.time())
    lat, lon = h3.cell_to_latlng(h3_index)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_visits_atomic(user_id, h3, ts, geom)
            VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ON CONFLICT DO NOTHING
            """,
            (user_id, h3_index, ts, lon, lat),
        )
        added = cur.rowcount > 0
        if not added:
            conn.rollback()
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

    conn.commit()
    return True


def _update_statistic(
    cur: psycopg2.extensions.cursor,
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
        INSERT INTO {table} (user_id, {key_field}, visited_cells, visited_weight)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(user_id, {key_field}) DO UPDATE SET
            visited_cells = {table}.visited_cells + %s,
            visited_weight = {table}.visited_weight + %s
        """,
        (user_id, region_id, increment_cell, coverage, increment_cell, coverage),
    )


def delete_visit_by_hex(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    h3_index: str,
) -> int:
    with conn.cursor() as cur:
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
            "DELETE FROM user_visits_atomic WHERE user_id = %s AND h3 = %s",
            (user_id, h3_index),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return 0

        if district_info:
            district_id, _ = district_info
            cur.execute(
                """
                UPDATE user_district_stats
                SET visited_cells = GREATEST(0, visited_cells - %s),
                    visited_weight = GREATEST(0.0, visited_weight - %s)
                WHERE user_id = %s AND district_id = %s
                """,
                (decrement_cell, coverage, user_id, district_id),
            )
            if okrug_id is not None:
                cur.execute(
                    """
                    UPDATE user_okrug_stats
                    SET visited_cells = GREATEST(0, visited_cells - %s),
                        visited_weight = GREATEST(0.0, visited_weight - %s)
                    WHERE user_id = %s AND okrug_id = %s
                    """,
                    (decrement_cell, coverage, user_id, okrug_id),
                )
    conn.commit()
    return 1


def update_visit_statistics(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    h3_index: str,
    district_id: int,
    coverage: float,
    okrug_id: Optional[int],
) -> bool:
    try:
        with conn.cursor() as cur:
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

        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        logger.error("Failed to update visit statistics: %s", exc)
        return False


def select_user_hexes_in_bbox(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT h3
            FROM user_visits_atomic
            WHERE user_id = %s
              AND geom IS NOT NULL
              AND ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
            """,
            (user_id, min_lon, min_lat, max_lon, max_lat),
        )
        return [str(row[0]) for row in cur.fetchall()]

