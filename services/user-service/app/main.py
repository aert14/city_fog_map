import os
import json
import hmac
import hashlib
import logging
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import db as db_module

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN not set - Telegram auth will not work")

# Debug mode toggle: when enabled, only debug auth endpoints are active
DEBUG_AUTH_MODE = os.getenv("DEBUG_AUTH_MODE", "0") == "1"
# No-auth local mode: bypass Telegram auth and use a fixed local user
NO_AUTH_MODE = os.getenv("NO_AUTH_MODE", "0") == "1"


def verify_init_data(raw_init_data: str, bot_token: str, max_age_sec: int = 86400) -> Dict:
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


def _get_user_from_session(request: Request) -> Optional[Tuple[int, Optional[str]]]:
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
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        raise HTTPException(status_code=500, detail="Server misconfigured: TELEGRAM_BOT_TOKEN not set")

    result = verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
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
    return user_id, username


def get_current_user(
    request: Request,
    telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init"),
) -> Tuple[int, Optional[str]]:
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
        logger.warning("No X-Telegram-Init header provided")
        raise HTTPException(status_code=401, detail="missing initData")

    return _get_user_from_header(telegram_init)


app = FastAPI(title="User Service API", version="0.1.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sessions for debug auth flow
SESSION_SECRET = os.getenv("SESSION_SECRET", os.urandom(32).hex())
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)


# Simple request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.perf_counter()
    # Log inbound request details (safe for sensitive headers)
    path_and_qs = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    client_host = getattr(getattr(request, "client", None), "host", "-")
    ua = request.headers.get("User-Agent", "-")
    ref = request.headers.get("Referer", "-")
    origin = request.headers.get("Origin", "-")
    init_header = request.headers.get("X-Telegram-Init")
    init_len = len(init_header) if init_header else 0
    init_hash = hashlib.sha256(init_header.encode()).hexdigest()[:12] if init_header else None
    logger.info(
        f"HTTP REQ {request.method} {path_and_qs} from {client_host} "
        f"ua='{ua[:120]}' ref='{ref[:160]}' origin='{origin[:120]}' "
        f"tg_init_present={bool(init_header)} tg_init_len={init_len} tg_init_sha256={init_hash or '-'}"
    )
    response = None
    error: Exception | None = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        error = exc
        raise
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        status_code = getattr(response, "status_code", 500 if error else "n/a")
        suffix = f" error={type(error).__name__}" if error else ""
        logger.info(
            f"HTTP RES {request.method} {path_and_qs} -> {status_code} in {duration_ms:.1f} ms{suffix}"
        )


@app.get("/health")
async def health() -> Dict[str, str]:
    logger.info("Health check requested")
    return {"status": "ok"}


# -------------------------
# Debug auth endpoints
# -------------------------

class AuthRequest(BaseModel):
    initData: str


class UserInfo(BaseModel):
    id: int
    tg_id: int
    username: Optional[str]


@app.post("/api/auth")
async def debug_auth(body: AuthRequest, request: Request):
    logger.info(f"/api/auth called; initData length={len(body.initData)}")
    result = verify_init_data(body.initData, TELEGRAM_BOT_TOKEN)
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


@app.get("/api/me")
async def debug_me(request: Request):
    if not request.session.get("tg_authenticated"):
        raise HTTPException(status_code=403, detail="unauthorized")
    logger.info(f"/api/me cookie keys: {list(request.cookies.keys())}")
    return {"ok": True, "user": request.session.get("tg_user")}


@app.get("/api/ping")
async def ping():
    return {"ok": True}


@app.get("/api/v1/debug-mode")
async def debug_mode():
    """Return debug mode status for frontend"""
    return {
        "debug_auth_mode": DEBUG_AUTH_MODE,
        "no_auth_mode": NO_AUTH_MODE,
    }


# -------------------------
# User management endpoints
# -------------------------

@app.get("/api/user/{user_id}", response_model=UserInfo)
async def get_user(user_id: int):
    """Get user information by internal user ID"""
    conn = db_module.get_connection()
    user_data = db_module.get_user_by_id(conn, user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    tg_id, username = user_data
    return UserInfo(id=user_id, tg_id=tg_id, username=username)


@app.post("/api/authenticate")
async def authenticate(user=Depends(get_current_user)) -> Dict[str, Any]:
    """Authenticate user and return user info with JWT-like token (simplified)"""
    user_id, username = user
    return {
        "user_id": user_id,
        "username": username,
        "authenticated": True
    }


# Dev utility: clear entire user database (allowed only in debug/no-auth)
@app.post("/api/v1/dev/clear-users")
async def dev_clear_users():
    if not (DEBUG_AUTH_MODE or NO_AUTH_MODE):
        raise HTTPException(status_code=403, detail="forbidden")
    conn = db_module.get_connection()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE users, user_settings RESTART IDENTITY")
        conn.commit()
    logger.warning("DEV clear-users executed")
    return {"cleared": True}
