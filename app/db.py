import os
import sqlite3
from typing import Optional, List, Tuple


DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "db.sqlite3"),
)

_CONNECTION: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    global _CONNECTION
    if _CONNECTION is None:
        _CONNECTION = sqlite3.connect(DB_PATH, check_same_thread=False)
        _CONNECTION.execute("PRAGMA journal_mode=WAL;")
        _CONNECTION.execute("PRAGMA synchronous=NORMAL;")
    return _CONNECTION


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL UNIQUE,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS circles (
            user_id INTEGER NOT NULL,
            geokey TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            radius_m INTEGER NOT NULL DEFAULT 100,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, geokey),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()


def ensure_user(conn: sqlite3.Connection, tg_id: int, username: Optional[str]) -> int:
    cur = conn.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    if row:
        user_id = int(row[0])
        # Update username opportunistically
        conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
        conn.commit()
        return user_id
    cur = conn.execute(
        "INSERT INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username)
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_circle_if_new(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    geokey: str,
    lat: float,
    lon: float,
    radius_m: int,
) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO circles (user_id, geokey, lat, lon, radius_m)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, geokey, float(lat), float(lon), int(radius_m)),
    )
    conn.commit()
    return cur.rowcount > 0


def count_circles(conn: sqlite3.Connection, *, user_id: int) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM circles WHERE user_id = ?", (user_id,))
    return int(cur.fetchone()[0])


def select_circles_in_bbox(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> List[Tuple[float, float, int, str]]:
    cur = conn.execute(
        """
        SELECT lat, lon, radius_m, geokey
        FROM circles
        WHERE user_id = ?
          AND lat BETWEEN ? AND ?
          AND lon BETWEEN ? AND ?
        ORDER BY created_at DESC
        LIMIT 10000
        """,
        (user_id, min_lat, max_lat, min_lon, max_lon),
    )
    return [(float(r[0]), float(r[1]), int(r[2]), str(r[3])) for r in cur.fetchall()]



def delete_circle_by_latlon(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    lat: float,
    lon: float,
) -> int:
    cur = conn.execute(
        """
        DELETE FROM circles
        WHERE user_id = ? AND ABS(lat - ?) < 1e-7 AND ABS(lon - ?) < 1e-7
        """,
        (user_id, float(lat), float(lon)),
    )
    conn.commit()
    return cur.rowcount


def update_radius_for_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    radius_m: int,
) -> int:
    cur = conn.execute(
        """
        UPDATE circles
        SET radius_m = ?
        WHERE user_id = ?
        """,
        (int(radius_m), int(user_id)),
    )
    conn.commit()
    return cur.rowcount


def update_radius_and_resolution_for_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    radius_m: int,
    h3_resolution: int,
) -> int:
    # First, ensure user has a record in a user_settings table or similar
    # For now, we'll store this in a simple key-value approach by updating all user's circles
    cur = conn.execute(
        """
        UPDATE circles
        SET radius_m = ?
        WHERE user_id = ?
        """,
        (int(radius_m), int(user_id)),
    )

    # Note: H3 resolution is computed on-the-fly in the application logic
    # We don't store it in the database, just compute it from radius when needed

    conn.commit()
    return cur.rowcount


def get_user_radius(conn: sqlite3.Connection, user_id: int) -> Optional[int]:
    """Get the current radius setting for a user"""
    cur = conn.execute(
        """
        SELECT radius_m
        FROM circles
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def get_user_h3_resolution(conn: sqlite3.Connection, user_id: int) -> Optional[int]:
    """Get the H3 resolution for a user based on their radius setting"""
    radius = get_user_radius(conn, user_id)
    if radius is None:
        return None

    # Map radius to H3 resolution (same logic as in main.py)
    if radius <= 30:
        return 13  # Small hexagons (~100m)
    elif radius <= 70:
        return 12  # Medium-small hexagons (~200m)
    elif radius <= 150:
        return 11  # Medium hexagons (~400m)
    else:
        return 10  # Large hexagons (~800m)


def clear_user_circles(conn: sqlite3.Connection, user_id: int) -> int:
    """Clear all circles for a user (used when H3 resolution changes)"""
    cur = conn.execute("DELETE FROM circles WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount

