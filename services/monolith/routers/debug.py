import json
import logging
from typing import Any, Dict, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Request

from services.common import database as db_module
from common import models

# Configure JSON logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["debug"])


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

    # Do not parse user JSON here to avoid side effects; just log payload keys
    logger.info(f"InitData verified successfully; payload keys: {sorted(list(data.keys()))}")
    return {"ok": True, "payload": data}


def _get_user_from_session(request: Request) -> Optional[tuple[int, str]]:
    """Аутентификация через сессию"""
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
            return user_id, username or ""
        except (ValueError, TypeError):
            logger.warning("Invalid user data in session", exc_info=True)
            return None
    return None


def _get_user_from_header(telegram_init: str) -> tuple[int, str]:
    """Аутентификация через header"""
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
    return user_id, username or ""


async def get_current_user(
    request: Request,
    telegram_init: Optional[str] = None,
) -> Tuple[int, str]:
    """Аутентификация пользователя"""
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


@router.post("/auth")
async def debug_auth(body: models.AuthRequest, request: Request):
    from services.monolith.main import TELEGRAM_BOT_TOKEN

    logger.info(f"/api/auth called; initData length={len(body.initData)}")
    result = _verify_init_data(body.initData, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        logger.warning(f"/api/auth unauthorized: {result.get('reason')}")
        raise HTTPException(status_code=403, detail="unauthorized")

    payload = result["payload"]
    user_raw = payload.get("user")
    user_obj = None
    if user_raw:
        try:
            user_obj = json.loads(user_raw)
        except json.JSONDecodeError:
            logger.warning("/api/auth: user JSON decode error")

    request.session["tg_authenticated"] = True
    request.session["tg_user_id"] = (user_obj or {}).get("id")
    request.session["tg_user"] = user_obj

    return {"ok": True}


@router.get("/me")
async def debug_me(request: Request):
    if not request.session.get("tg_authenticated"):
        raise HTTPException(status_code=403, detail="unauthorized")
    logger.info(f"/api/me cookie keys: {list(request.cookies.keys())}")
    return {"ok": True, "user": request.session.get("tg_user")}


@router.post("/authenticate")
async def authenticate(user=Depends(get_current_user)) -> Dict[str, Any]:
    """Authenticate user and return user info with JWT-like token (simplified)"""
    user_id, username = user
    return {
        "user_id": user_id,
        "username": username,
        "authenticated": True
    }


@router.get("/ping")
async def ping():
    return {"ok": True}


@router.get("/v1/debug-mode")
async def debug_mode():
    """Return debug mode status for frontend"""
    from services.monolith.main import DEBUG_AUTH_MODE, NO_AUTH_MODE

    return {
        "debug_auth_mode": DEBUG_AUTH_MODE,
        "no_auth_mode": NO_AUTH_MODE,
        "base_visit_resolution": db_module.BASE_VISIT_RESOLUTION,
    }


# Dev utility: clear entire database (allowed only in debug/no-auth)
@router.post("/v1/dev/clear-db")
async def dev_clear_db():
    from services.monolith.main import DEBUG_AUTH_MODE, NO_AUTH_MODE

    if not (DEBUG_AUTH_MODE or NO_AUTH_MODE):
        raise HTTPException(status_code=403, detail="forbidden")
    conn = db_module.get_connection()
    cleared_circles, cleared_users = db_module.clear_all(conn)
    logger.warning(f"DEV clear-db executed: circles={cleared_circles}, users={cleared_users}")
    return {"cleared_circles": int(cleared_circles), "cleared_users": int(cleared_users)}
