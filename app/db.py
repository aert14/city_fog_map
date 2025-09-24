"""
Data access layer for the City Fog Map application.

This module handles all interactions with the SQLite database, including
connection management, schema initialization, and CRUD (Create, Read, Update,
Delete) operations for all application data.

The database connection is managed as a singleton to ensure that a single,
consistent connection is used throughout the application's lifecycle.
"""
import os
import sqlite3
from typing import Optional, List, Tuple


# Determine the database path from an environment variable or use a default.
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "db.sqlite3"),
)

# Global variable to hold the single database connection.
_CONNECTION: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    """
    Establishes and returns a singleton database connection.

    On the first call, it creates a new connection to the SQLite database and
    configures it for performance with WAL (Write-Ahead Logging) mode.
    Subsequent calls return the existing connection.

    Returns:
        The active sqlite3.Connection object.
    """
    global _CONNECTION
    if _CONNECTION is None:
        _CONNECTION = sqlite3.connect(DB_PATH, check_same_thread=False)
        # Enable WAL mode for better concurrency and performance.
        _CONNECTION.execute("PRAGMA journal_mode=WAL;")
        _CONNECTION.execute("PRAGMA synchronous=NORMAL;")
    return _CONNECTION


def init_db(conn: sqlite3.Connection) -> None:
    """
    Initializes the database by creating tables if they don't already exist.

    Args:
        conn: The database connection object.
    """
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
    """
    Ensures a user exists in the database and returns their internal ID.

    If the user with the given Telegram ID already exists, their username is
    updated if a new one is provided. If the user does not exist, a new entry
    is created in the `users` table, and default settings are created in the
    `user_settings` table.

    Args:
        conn: The database connection object.
        tg_id: The user's Telegram ID.
        username: The user's optional Telegram username.

    Returns:
        The internal, auto-incrementing user ID.
    """
    cur = conn.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    if row:
        user_id = int(row[0])
        # Opportunistically update the username if it has changed.
        if username:
            conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
            conn.commit()
        return user_id

    # If the user doesn't exist, create them.
    cur = conn.execute(
        "INSERT INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username)
    )
    user_id = int(cur.lastrowid)

    # Create default settings for the new user.
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
    """
    Inserts a new explored circle into the database for a user.

    The combination of `user_id` and `geokey` is unique. If a circle with the
    same key already exists for the user, this operation does nothing.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.
        geokey: The H3 geohash for the circle's center.
        lat: The latitude of the circle's center.
        lon: The longitude of the circle's center.
        radius_m: The radius of the circle in meters.

    Returns:
        True if a new circle was inserted, False otherwise.
    """
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
    """
    Counts the total number of explored circles for a specific user.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.

    Returns:
        The total count of circles for that user.
    """
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
    """
    Selects all explored circles for a user within a given bounding box.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.
        min_lat: The minimum latitude of the bounding box.
        min_lon: The minimum longitude of the bounding box.
        max_lat: The maximum latitude of the bounding box.
        max_lon: The maximum longitude of the bounding box.

    Returns:
        A list of tuples, where each tuple represents a circle and contains
        (latitude, longitude, radius, geokey).
    """
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
    """
    Deletes a specific explored circle for a user, identified by its geokey.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.
        geokey: The H3 geohash of the circle to delete.

    Returns:
        The number of rows deleted (0 or 1).
    """
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
    """
    Updates the exploration radius and H3 resolution for a user.

    This uses an "UPSERT" operation to either create a new settings entry or
    update the existing one for the given user.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.
        radius_m: The new exploration radius in meters.
        h3_resolution: The new H3 resolution corresponding to the radius.

    Returns:
        The number of rows modified.
    """
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
    """
    Retrieves the current exploration radius for a user.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.

    Returns:
        The user's current radius in meters, or a default value if not set.
    """
    cur = conn.execute(
        "SELECT radius_m FROM user_settings WHERE user_id = ?", (user_id,)
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    # Fallback to a default value if settings don't exist for some reason.
    return 50


def get_user_h3_resolution(conn: sqlite3.Connection, user_id: int) -> int:
    """
    Retrieves the current H3 resolution for a user.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user.

    Returns:
        The user's current H3 resolution, or a default value if not set.
    """
    cur = conn.execute(
        "SELECT h3_resolution FROM user_settings WHERE user_id = ?", (user_id,)
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    # Fallback to a default value.
    return 11


def clear_user_circles(conn: sqlite3.Connection, user_id: int) -> int:
    """
    Deletes all explored circles for a specific user.

    This is typically used when the user changes their H3 resolution, which
    invalidates all previously explored circles.

    Args:
        conn: The database connection object.
        user_id: The internal ID of the user whose circles will be cleared.

    Returns:
        The number of circles deleted.
    """
    cur = conn.execute("DELETE FROM circles WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount


def clear_all(conn: sqlite3.Connection) -> Tuple[int, int]:
    """
    Deletes all data from the `circles` and `users` tables.

    This is a destructive operation intended for debugging and testing.

    Args:
        conn: The database connection object.

    Returns:
        A tuple containing the number of deleted circles and users.
    """
    cur = conn.execute("SELECT COUNT(*) FROM circles")
    circles_deleted = int(cur.fetchone()[0])
    cur = conn.execute("SELECT COUNT(*) FROM users")
    users_deleted = int(cur.fetchone()[0])

    conn.execute("DELETE FROM circles")
    conn.execute("DELETE FROM users")
    conn.execute("DELETE FROM user_settings")
    conn.commit()
    return circles_deleted, users_deleted
