from __future__ import annotations

from typing import List, Optional, Tuple

import psycopg2
import psycopg2.extras

from .connection import BASE_VISIT_RESOLUTION


def ensure_user(
    conn: psycopg2.extensions.connection,
    tg_id: int,
    username: Optional[str],
) -> int:
    max_retries = 3
    for attempt in range(max_retries):
        try:
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
        except psycopg2.errors.DeadlockDetected:
            if attempt < max_retries - 1:
                import time

                time.sleep(0.1 * (attempt + 1))
                continue
            raise


def get_user_by_id(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> Optional[Tuple[int, Optional[str]]]:
    with conn.cursor() as cur:
        cur.execute("SELECT tg_id, username FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            return int(row[0]), row[1]
    return None


def insert_circle_if_new(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    geokey: str,
    lat: float,
    lon: float,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO circles (user_id, geokey, lat, lon)
            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (user_id, geokey, float(lat), float(lon)),
        )
        conn.commit()
        return cur.rowcount > 0


def count_circles(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM circles WHERE user_id = %s", (user_id,))
        return int(cur.fetchone()[0])


def select_circles_in_bbox(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> List[Tuple[float, float, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT lat, lon, geokey
            FROM circles
            WHERE user_id = %s
              AND lat BETWEEN %s AND %s
              AND lon BETWEEN %s AND %s
            ORDER BY created_at DESC
            LIMIT 10000
            """,
            (user_id, min_lat, max_lat, min_lon, max_lon),
        )
        return [(float(r[0]), float(r[1]), str(r[2])) for r in cur.fetchall()]


def delete_circle_by_geokey(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    geokey: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM circles WHERE user_id = %s AND geokey = %s",
            (user_id, geokey),
        )
        conn.commit()
        return cur.rowcount


def update_user_h3_resolution(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int,
    h3_resolution: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_settings (user_id, h3_resolution)
            VALUES (%s, %s)
            ON CONFLICT(user_id) DO UPDATE SET
                h3_resolution = EXCLUDED.h3_resolution;
            """,
            (user_id, h3_resolution),
        )
        conn.commit()
        return cur.rowcount


def get_user_h3_resolution(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h3_resolution FROM user_settings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
    return BASE_VISIT_RESOLUTION


def clear_user_circles(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM circles WHERE user_id = %s", (user_id,))
        conn.commit()
        return cur.rowcount


def clear_all(conn: psycopg2.extensions.connection) -> Tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM circles")
        count_circles_rows = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM users")
        count_users = int(cur.fetchone()[0])

        cur.execute(
            "TRUNCATE circles, users, user_settings, user_visits_atomic, user_district_stats, user_okrug_stats, user_achievements RESTART IDENTITY",
        )
        conn.commit()
        return count_circles_rows, count_users


def check_and_grant_achievements(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> None:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) as visit_count FROM user_visits_atomic WHERE user_id = %s",
            (user_id,),
        )
        visit_count = cur.fetchone()["visit_count"]

        cur.execute("SELECT id, code FROM achievements")
        achievements = {row["code"]: row["id"] for row in cur.fetchall()}

        to_grant = []
        if visit_count >= 1 and "FIRST_STEP" in achievements:
            to_grant.append(achievements["FIRST_STEP"])
        if visit_count >= 100 and "EXPLORER_100" in achievements:
            to_grant.append(achievements["EXPLORER_100"])
        if visit_count >= 1000 and "CARTOGRAPHER_1000" in achievements:
            to_grant.append(achievements["CARTOGRAPHER_1000"])

        if to_grant:
            args_str = ",".join(
                cur.mogrify("(%s,%s)", (user_id, ach_id)).decode("utf-8")
                for ach_id in to_grant
            )
            cur.execute(
                f"""
                INSERT INTO user_achievements (user_id, achievement_id) VALUES {args_str}
                ON CONFLICT (user_id, achievement_id) DO NOTHING
                """
            )
    conn.commit()


def select_user_hexes(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h3 FROM user_visits_atomic WHERE user_id = %s",
            (user_id,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def count_user_visited_hexes(
    conn: psycopg2.extensions.connection,
    user_id: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM user_visits_atomic WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

