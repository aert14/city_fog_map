import os
import sqlite3
from typing import Optional, List


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

    # Drop the old 'circles' table if it exists to ensure a clean migration
    conn.execute("DROP TABLE IF EXISTS circles;")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hexagons (
            user_id INTEGER NOT NULL,
            geokey TEXT NOT NULL,
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
        if username:
            conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
            conn.commit()
        return user_id
    cur = conn.execute(
        "INSERT INTO users (tg_id, username) VALUES (?, ?)", (tg_id, username)
    )
    conn.commit()
    # Using mypy hint to ensure lastrowid is not None
    return int(cur.lastrowid) if cur.lastrowid is not None else -1


def insert_hexagon_if_new(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    geokey: str,
) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO hexagons (user_id, geokey)
        VALUES (?, ?)
        """,
        (user_id, geokey),
    )
    conn.commit()
    return cur.rowcount > 0


def count_hexagons(conn: sqlite3.Connection, *, user_id: int) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM hexagons WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def select_hexagons_by_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
) -> List[str]:
    cur = conn.execute(
        """
        SELECT geokey
        FROM hexagons
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 20000
        """,
        (user_id,),
    )
    return [str(r[0]) for r in cur.fetchall()]
