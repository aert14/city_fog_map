import os
import sqlite3
from typing import Optional, List, Tuple

from . import utils


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
            radius_m INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, geokey),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            radius_m INTEGER NOT NULL DEFAULT 50,
            h3_resolution INTEGER NOT NULL DEFAULT 11,
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
        if username:
            conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
            conn.commit()
        return user_id

    # Create new user
    cur = conn.execute(
        "INSERT INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username)
    )
    user_id = int(cur.lastrowid)

    # Create default settings for the new user
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    conn.commit()

    return user_id


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



def delete_circle_by_geokey(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    geokey: str,
) -> int:
    """Deletes a circle by its geokey for a specific user."""
    cur = conn.execute(
        "DELETE FROM circles WHERE user_id = ? AND geokey = ?",
        (user_id, geokey),
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
    """Update a user's radius and H3 resolution in the user_settings table."""
    cur = conn.execute(
        """
        INSERT INTO user_settings (user_id, radius_m, h3_resolution)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            radius_m = excluded.radius_m,
            h3_resolution = excluded.h3_resolution;
        """,
        (user_id, radius_m, h3_resolution),
    )
    conn.commit()
    return cur.rowcount


def get_user_radius(conn: sqlite3.Connection, user_id: int) -> int:
    """Get the current radius setting for a user from user_settings."""
    cur = conn.execute(
        "SELECT radius_m FROM user_settings WHERE user_id = ?", (user_id,)
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    # Fallback to default if no settings exist for some reason
    return 50


def get_user_h3_resolution(conn: sqlite3.Connection, user_id: int) -> int:
    """Get the H3 resolution for a user from user_settings."""
    cur = conn.execute(
        "SELECT h3_resolution FROM user_settings WHERE user_id = ?", (user_id,)
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    # Fallback to default
    return 11


def clear_user_circles(conn: sqlite3.Connection, user_id: int) -> int:
    """Clear all circles for a user (used when H3 resolution changes)"""
    cur = conn.execute("DELETE FROM circles WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount


def clear_all(conn: sqlite3.Connection) -> tuple[int, int]:
    """Delete all rows from circles and users tables.

    Returns:
        (deleted_circles, deleted_users)
    """
    cur_c = conn.execute("SELECT COUNT(*) FROM circles")
    cur_u = conn.execute("SELECT COUNT(*) FROM users")
    count_circles = int(cur_c.fetchone()[0])
    count_users = int(cur_u.fetchone()[0])

    conn.execute("DELETE FROM circles")
    conn.execute("DELETE FROM users")
    conn.commit()
    return count_circles, count_users

