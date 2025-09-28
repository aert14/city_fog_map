import os
import json
import hmac
import hashlib
import urllib.parse
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pythonjsonlogger.jsonlogger import JsonFormatter

from services.common import database as db_module
import cache
import tracing
import queue

# Import routers
from .routers import visits, districts, stats, users, debug


# Configure JSON logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Create JSON formatter
json_formatter = JsonFormatter()

# Create stream handler for stdout
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(json_formatter)

# Add handler to logger
logger.addHandler(stream_handler)

# Setup OpenTelemetry tracing
tracing.setup_tracing("monolith")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    # Do not crash on import; raise on first guarded route.
    pass

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


async def get_current_user(
    request: Request,
    telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init"),
) -> Tuple[int, Optional[str]]:
    logger.info("Authenticating user")

    if NO_AUTH_MODE:
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="local")
        logger.warning("NO_AUTH_MODE enabled: bypassing auth, using local user")
        return user_id, "local"

    # Try session authentication first
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

    if DEBUG_AUTH_MODE:
        raise HTTPException(status_code=503, detail="Authentication via session only in debug auth mode")

    if not telegram_init:
        logger.warning("No X-Telegram-Init header provided")
        raise HTTPException(status_code=401, detail="missing initData")

    # Fallback to header authentication
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 City Fog Map API starting up...")

    # Check for production environment with debug flags
    app_env = os.getenv("APP_ENV", "").lower()
    if app_env == "production" and (DEBUG_AUTH_MODE or NO_AUTH_MODE):
        logger.error("FATAL: Cannot start in production environment with debug flags enabled")
        raise RuntimeError("Cannot start in production environment with DEBUG_AUTH_MODE or NO_AUTH_MODE enabled")

    # Initialize Redis
    await cache.init_redis_pool()

    # Initialize RabbitMQ
    await queue.init_rabbitmq_connection()

    # Initialize database
    logger.info("Database initialization...")
    conn = db_module.get_connection()
    db_module.init_db(conn)
    logger.info("Database initialized successfully")

    yield

    # Shutdown
    logger.info("🛑 City Fog Map API shutting down...")

    # Close Redis connection
    await cache.close_redis_pool()

    # Close RabbitMQ connection
    await queue.close_rabbitmq_connection()

app = FastAPI(title="City Fog Map API", version="0.1.0", lifespan=lifespan)

# Add Prometheus metrics
from prometheus_fastapi_instrumentator import Instrumentator
instrumentator = Instrumentator()
instrumentator.instrument(app)
instrumentator.expose(app)

# Static frontend at /webapp
webapp_dir = os.path.join(os.path.dirname(__file__), "../../webapp")
if not os.path.isdir(webapp_dir):
    os.makedirs(webapp_dir, exist_ok=True)
class LongCacheStatic(StaticFiles):
    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        # Index handled separately; here set long cache for assets
        if response.status_code == 200 and response.media_type != "text/html":
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response

app.mount("/webapp", LongCacheStatic(directory=webapp_dir, html=True), name="webapp")

# Version for cache-busting static assets
APP_VERSION = os.getenv("APP_VERSION")
if not APP_VERSION:
    try:
        app_js_path = os.path.join(webapp_dir, "app.js")
        APP_VERSION = str(int(os.path.getmtime(app_js_path)))
    except Exception:
        APP_VERSION = str(int(time.time()))

def _read_index_with_version() -> str:
    index_path = os.path.join(webapp_dir, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        # inject version to app.js, style.css, and fog.js
        html = html.replace("/webapp/app.js", f"/webapp/app.js?v={APP_VERSION}")
        html = html.replace("/webapp/style.css", f"/webapp/style.css?v={APP_VERSION}")
        html = html.replace("/webapp/fog.js", f"/webapp/fog.js?v={APP_VERSION}")
        return html
    except Exception as e:
        logger.error(f"Failed to read index.html: {e}")
        return "<html><body>index missing</body></html>"

# Redirect root to the appropriate page
@app.get("/")
async def root_redirect():
    if DEBUG_AUTH_MODE:
        return RedirectResponse(url="/webapp/debug-auth.html")
    return RedirectResponse(url="/webapp/")

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




@app.get("/webapp/")
async def webapp_index() -> Response:
    html = _read_index_with_version()
    injection = f'<script>window.__CITY_FOG_BASE_RESOLUTION__ = {db_module.BASE_VISIT_RESOLUTION};</script>'
    marker = '<script src="/webapp/app.js"></script>'
    if marker in html:
        html = html.replace(marker, f"{injection}\n    {marker}")
    else:
        html = f"{html}\n{injection}"
    headers = {"Cache-Control": "no-store"}
    return Response(content=html, media_type="text/html; charset=utf-8", headers=headers)


@app.get("/health")
async def health() -> Dict[str, str]:
    logger.info("Health check requested")
    return {"status": "ok"}


# Include routers
app.include_router(visits.router)
app.include_router(districts.router)
app.include_router(stats.router)
app.include_router(users.router)
app.include_router(debug.router)