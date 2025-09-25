import os
import psycopg2
import psycopg2.extras
from typing import Optional, Tuple

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

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


def get_user_by_id(conn: psycopg2.extensions.connection, user_id: int) -> Optional[Tuple[int, Optional[str]]]:
    with conn.cursor() as cur:
        cur.execute("SELECT tg_id, username FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            return int(row[0]), row[1]
        return None
