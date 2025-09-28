from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from .connection import PRIMARY_COVERAGE_THRESHOLD
from .queries_users import count_user_visited_hexes


logger = logging.getLogger(__name__)


def fetch_user_stats(
    conn: psycopg2.extensions.connection,
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
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        if district_id is not None:
            cur.execute(
                """
                SELECT visited_cells, visited_weight
                FROM user_district_stats
                WHERE user_id = %s AND district_id = %s
                """,
                (user_id, district_id),
            )
            row = cur.fetchone()
            if row:
                stats["district"] = {
                    "id": district_id,
                    "visited_cells": int(row["visited_cells"]),
                    "visited_weight": float(row["visited_weight"]),
                }
        if okrug_id is not None:
            cur.execute(
                """
                SELECT visited_cells, visited_weight
                FROM user_okrug_stats
                WHERE user_id = %s AND okrug_id = %s
                """,
                (user_id, okrug_id),
            )
            row = cur.fetchone()
            if row:
                stats["okrug"] = {
                    "id": okrug_id,
                    "visited_cells": int(row["visited_cells"]),
                    "visited_weight": float(row["visited_weight"]),
                }
    return stats


def fetch_user_total_progress(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> Dict[str, float]:
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


def fetch_user_okrug_progress(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> List[Dict[str, Any]]:
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
    conn: psycopg2.extensions.connection,
    user_id: int,
    limit: int = 3,
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
    conn: psycopg2.extensions.connection,
    *,
    level: str,
) -> Tuple[int, float]:
    if level not in {"district", "okrug"}:
        raise ValueError("Unsupported level")

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
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
            row = cur.fetchone()
            if not row:
                return 0, 0.0
            return int(row["total_cells"] or 0), float(row["total_weight"] or 0.0)

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

    now_ts = int(time.time())
    period_seconds = 7 * 24 * 3600 if period == "week" else 90 * 24 * 3600
    since_ts = max(0, now_ts - period_seconds)

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
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
        """,
            [PRIMARY_COVERAGE_THRESHOLD, since_ts, level, limit],
        )
        return [dict(row) for row in cur.fetchall()]
