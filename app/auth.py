"""
Handles user authentication for the City Fog Map application.

This module provides functions to verify user identity based on Telegram's
initData, session cookies (for debugging), or a no-auth mode for local
development. It is designed to be used as a dependency in FastAPI endpoints.
"""
import os
import json
import hmac
import hashlib
import urllib.parse
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict

from fastapi import Depends, Header, HTTPException, Request

from . import db as db_module

logger = logging.getLogger(__name__)

# --- Authentication Configuration ---

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# When True, authentication is bypassed and a fixed local user is assumed.
# This is useful for frontend development in a regular web browser.
NO_AUTH_MODE = os.getenv("NO_AUTH_MODE", "0") == "1"

# When True, authentication is handled via a session cookie set by a debug endpoint.
# This is useful for testing the authentication flow itself.
# If NO_AUTH_MODE is also True, it takes precedence.
DEBUG_AUTH_MODE = os.getenv("DEBUG_AUTH_MODE", "0") == "1"


def verify_init_data(raw_init_data: str, bot_token: str, max_age_sec: int = 86400) -> Dict:
    """
    Verifies the authenticity of Telegram's initData string.

    The verification process follows the steps outlined in Telegram's documentation:
    1. Check that the `hash` field is present.
    2. Sort and format the other fields into a data-check-string.
    3. Calculate the HMAC-SHA256 hash of the data-check-string using the bot token.
    4. Compare the calculated hash with the received hash.
    5. Check that the `auth_date` is not older than `max_age_sec`.

    Args:
        raw_init_data: The raw initData string from the Telegram client.
        bot_token: The secret token of the Telegram bot.
        max_age_sec: The maximum allowed age of the initData in seconds.

    Returns:
        A dictionary with "ok": True and a "payload" on success, or "ok": False
        and a "reason" on failure.
    """
    logger.info(f"Verifying initData: {len(raw_init_data)} chars")
    data = dict(urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        logger.warning("Missing hash in initData")
        return {"ok": False, "reason": "missing hash"}

    # Construct the data-check-string
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    # Calculate the expected hash
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # Compare hashes
    if not hmac.compare_digest(expected_hash, received_hash):
        logger.warning(f"Hash mismatch: expected {expected_hash[:16]}..., got {received_hash[:16]}...")
        return {"ok": False, "reason": "hash mismatch"}

    # Check the timestamp
    try:
        auth_ts = int(data.get("auth_date", "0"))
        if auth_ts <= 0:
            raise ValueError("auth_date is zero or negative")

        auth_dt = datetime.fromtimestamp(auth_ts, tz=timezone.utc)
        if datetime.now(timezone.utc) - auth_dt > timedelta(seconds=max_age_sec):
            logger.warning(f"Stale auth_date: {auth_dt}")
            return {"ok": False, "reason": "stale auth_date"}
    except (ValueError, TypeError):
        logger.warning(f"Invalid auth_date format: {data.get('auth_date')}", exc_info=True)
        return {"ok": False, "reason": "bad auth_date"}

    logger.info(f"InitData verified successfully; payload keys: {sorted(list(data.keys()))}")
    return {"ok": True, "payload": data}


def _get_user_from_session(request: Request) -> Optional[Tuple[int, Optional[str]]]:
    """
    Authenticates a user based on session data (used in DEBUG_AUTH_MODE).

    Returns:
        A tuple of (internal_user_id, username) if the session is valid,
        otherwise None.
    """
    if request.session.get("tg_authenticated") and request.session.get("tg_user_id"):
        try:
            tg_id = int(request.session["tg_user_id"])
            tg_user = request.session.get("tg_user", {})
            username = tg_user.get("username") if isinstance(tg_user, dict) else None

            conn = db_module.get_connection()
            user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
            logger.info(f"User authenticated via session: user_id={user_id}, tg_id={tg_id}")
            return user_id, username
        except (ValueError, TypeError):
            logger.warning("Invalid user data in session", exc_info=True)
            return None
    return None


def _get_user_from_header(telegram_init: str) -> Tuple[int, Optional[str]]:
    """
    Authenticates a user based on the X-Telegram-Init header.

    Returns:
        A tuple of (internal_user_id, username).
    Raises:
        HTTPException if authentication fails.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("CRITICAL: TELEGRAM_BOT_TOKEN is not set!")
        raise HTTPException(status_code=500, detail="Server misconfigured")

    result = verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        raise HTTPException(status_code=401, detail=f"Invalid initData: {result.get('reason')}")

    payload = result["payload"]
    user_raw = payload.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="No user object in initData")

    try:
        user_obj = json.loads(user_raw)
        tg_id = int(user_obj["id"])
        username = user_obj.get("username")
    except (json.JSONDecodeError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user JSON in initData")

    conn = db_module.get_connection()
    user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
    logger.info(f"User authenticated via header: user_id={user_id}, tg_id={tg_id}")
    return user_id, username


async def get_current_user(
    request: Request,
    telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init"),
) -> Tuple[int, Optional[str]]:
    """
    FastAPI dependency to get the current authenticated user.

    This function implements the primary authentication strategy based on the
    server's operating mode:
    1.  **NO_AUTH_MODE:** Bypasses all checks and returns a fixed local user.
    2.  **DEBUG_AUTH_MODE:** Attempts to authenticate using a session cookie.
    3.  **Standard Mode:** Authenticates using the X-Telegram-Init header.

    Returns:
        A tuple containing the internal user_id and the username.
    Raises:
        HTTPException if no valid authentication method succeeds.
    """
    logger.info("Authenticating user...")

    # Highest priority: No-auth mode for local development.
    if NO_AUTH_MODE:
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="local_user")
        logger.warning("NO_AUTH_MODE enabled: Bypassing auth, using local user.")
        return user_id, "local_user"

    # Second priority: Session-based auth for debugging.
    if DEBUG_AUTH_MODE:
        session_user = _get_user_from_session(request)
        if session_user:
            return session_user
        # In debug mode, we only allow session auth.
        raise HTTPException(status_code=401, detail="Not authenticated via session in debug mode.")

    # Standard mode: Header-based authentication.
    if not telegram_init:
        logger.warning("Authentication failed: X-Telegram-Init header is missing.")
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init header")

    return _get_user_from_header(telegram_init)
