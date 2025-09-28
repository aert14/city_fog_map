import logging
from typing import List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException
import psycopg2.extras

from services.common import database as db_module
from common import models

# Configure JSON logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["users"])


def _get_user_from_session(request) -> Optional[Tuple[int, Optional[str]]]:
    """Аутентификация через сессию - упрощенная версия"""
    from fastapi import Request
    if request.session.get("tg_authenticated") and request.session.get("tg_user_id"):
        try:
            tg_id = int(request.session["tg_user_id"])
            tg_user = request.session.get("tg_user", {})
            username = tg_user.get("username") if isinstance(tg_user, dict) else None

            conn = db_module.get_connection()
            user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
            logger.info(
                "User authenticated via session: user_id=%s, tg_id=%s, username=%s",
                user_id,
                tg_id,
                username,
            )
            return user_id, username
        except (ValueError, TypeError):
            logger.warning("Invalid user data in session", exc_info=True)
            return None
    return None


def _get_user_from_header(telegram_init: str) -> Tuple[int, Optional[str]]:
    """Аутентификация через header - упрощенная версия"""
    from services.monolith.main import TELEGRAM_BOT_TOKEN

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        raise HTTPException(status_code=500, detail="Server misconfigured: TELEGRAM_BOT_TOKEN not set")

    result = _verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        raise HTTPException(status_code=401, detail=result.get("reason", "bad initData"))

    payload = result["payload"]
    user_raw = payload.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="no user in initData")

    try:
        import json
        user_obj = json.loads(user_raw)
        tg_id = int(user_obj["id"])
        username = user_obj.get("username")
    except (json.JSONDecodeError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="bad user json")

    conn = db_module.get_connection()
    user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
    logger.info(
        "User authenticated via header: user_id=%s, tg_id=%s, username=%s",
        user_id,
        tg_id,
        username,
    )
    return user_id, username


def _verify_init_data(raw_init_data: str, bot_token: str, max_age_sec: int = 86400) -> dict:
    """Верификация данных от Telegram"""
    import urllib.parse
    import hmac
    import hashlib
    from datetime import datetime, timezone, timedelta

    logger.info(f"Verifying initData: {len(raw_init_data)} chars")
    data = dict(urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True))
    recv_hash = data.pop("hash", None)
    if not recv_hash:
        logger.warning("Missing hash in initData")
        return {"ok": False, "reason": "missing hash"}

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    exp_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(exp_hash, recv_hash):
        logger.warning(f"Hash mismatch: expected {exp_hash[:16]}..., got {recv_hash[:16]}...")
        return {"ok": False, "reason": "hash mismatch"}

    try:
        auth_ts = int(data.get("auth_date", "0"))
    except ValueError:
        logger.warning(f"Invalid auth_date format: {data.get('auth_date')}")
        return {"ok": False, "reason": "bad auth_date"}
    if auth_ts <= 0:
        logger.warning(f"Empty auth_date: {auth_ts}")
        return {"ok": False, "reason": "empty auth_date"}
    if datetime.now(timezone.utc) - datetime.fromtimestamp(auth_ts, tz=timezone.utc) > timedelta(seconds=max_age_sec):
        logger.warning(f"Stale auth_date: {auth_ts}")
        return {"ok": False, "reason": "stale auth_date"}

    logger.info(f"InitData verified successfully; payload keys: {sorted(list(data.keys()))}")
    return {"ok": True, "payload": data}


async def get_current_user(
    request,  # Упрощено для совместимости
    telegram_init: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """Аутентификация пользователя - упрощенная версия для роутера"""
    from services.monolith.main import NO_AUTH_MODE, DEBUG_AUTH_MODE

    logger.info("Authenticating user")

    if NO_AUTH_MODE:
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="local")
        logger.warning("NO_AUTH_MODE enabled: bypassing auth, using local user")
        return user_id, "local"

    session_user = _get_user_from_session(request)
    if session_user:
        return session_user

    if DEBUG_AUTH_MODE:
        raise HTTPException(status_code=503, detail="Authentication via session only in debug auth mode")

    if not telegram_init:
        logger.warning("No telegram_init provided")
        raise HTTPException(status_code=401, detail="missing initData")

    return _get_user_from_header(telegram_init)


@router.get("/me/achievements", response_model=List[models.Achievement])
async def get_my_achievements(user=Depends(get_current_user)):
    user_id, _ = user
    conn = db_module.get_connection()
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT
                a.id, a.code, a.name, a.description, a.icon,
                (ua.user_id IS NOT NULL) as unlocked,
                ua.created_at
            FROM achievements AS a
            LEFT JOIN user_achievements AS ua
                ON a.id = ua.achievement_id AND ua.user_id = %s
            ORDER BY a.id;
            """,
            (user_id,)
        )
        rows = cur.fetchall()
        return [dict(row) for row in rows]


@router.get("/user/{user_id}", response_model=models.UserInfo)
async def get_user(user_id: int):
    """Get user information by internal user ID"""
    conn = db_module.get_connection()
    user_data = db_module.get_user_by_id(conn, user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    tg_id, username = user_data
    return models.UserInfo(id=user_id, tg_id=tg_id, username=username)
