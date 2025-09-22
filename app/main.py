import os
import json
import hmac
import hashlib
import urllib.parse
import logging
from logging.handlers import RotatingFileHandler
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
import h3
from starlette.middleware.sessions import SessionMiddleware

from . import db as db_module


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# File logging to project root: /home/aert141414/city_fog_map/server.log
try:
    project_root = os.path.dirname(os.path.dirname(__file__))
    log_file_path = os.path.join(project_root, "server.log")
    need_handler = True
    for h in logging.getLogger().handlers:
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == os.path.abspath(log_file_path):
            need_handler = False
            break
    if need_handler:
        fh = RotatingFileHandler(log_file_path, maxBytes=1_000_000, backupCount=3)
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(fh)
        logger.info(f"File logging enabled at {log_file_path}")
except Exception as e:
    logger.warning(f"Failed to set up file logging: {e}")


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


class VisitRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)


class VisitResponse(BaseModel):
    added: int
    stats: Dict[str, int]


class HexagonsResponse(BaseModel):
    hexagons: List[str]


async def get_current_user(request: Request, telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init")) -> Tuple[int, Optional[str]]:
    logger.info("Authenticating user")

    # 0) No-auth local mode (for development only)
    if NO_AUTH_MODE:
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="local")
        logger.warning("NO_AUTH_MODE enabled: bypassing auth, using local user")
        return user_id, "local"

    # 1) Try session-based auth (set by /api/auth)
    if request.session.get("tg_authenticated") and request.session.get("tg_user_id"):
        try:
            tg_id = int(request.session.get("tg_user_id"))
        except Exception:
            tg_id = None
        tg_user = request.session.get("tg_user") or {}
        username = tg_user.get("username") if isinstance(tg_user, dict) else None
        if tg_id:
            conn = db_module.get_connection()
            user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
            logger.info(f"User authenticated via session: user_id={user_id}, tg_id={tg_id}, username={username}")
            return user_id, username

    # 2) Fallback to header-based auth (Telegram WebApp initData per request)
    if DEBUG_AUTH_MODE:
        # In debug mode, normal endpoints are disabled; keep behavior consistent
        raise HTTPException(status_code=503, detail="authentication disabled in debug auth mode")

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        raise HTTPException(status_code=500, detail="Server misconfigured: TELEGRAM_BOT_TOKEN not set")
    if not telegram_init:
        logger.warning("No X-Telegram-Init header provided")
        raise HTTPException(status_code=401, detail="missing initData")

    result = verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        raise HTTPException(status_code=401, detail=result.get("reason", "bad initData"))

    payload = result["payload"]
    user_raw = payload.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="no user in initData")
    try:
        user_obj = json.loads(user_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="bad user json")

    tg_id = int(user_obj.get("id"))
    username = user_obj.get("username")

    conn = db_module.get_connection()
    user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
    logger.info(f"User authenticated via header: user_id={user_id}, tg_id={tg_id}, username={username}")
    return user_id, username


app = FastAPI(title="City Fog Map API", version="0.1.0")

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 City Fog Map API starting up...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 City Fog Map API shutting down...")

# Static frontend at /webapp
webapp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
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
        # inject version to app.js
        html = html.replace("/webapp/app.js", f"/webapp/app.js?v={APP_VERSION}")
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


@app.on_event("startup")
def on_startup() -> None:
    logger.info("Database initialization...")
    conn = db_module.get_connection()
    db_module.init_db(conn)
    logger.info("Database initialized successfully")


@app.get("/webapp/")
async def webapp_index() -> Response:
    html = _read_index_with_version()
    headers = {"Cache-Control": "no-store"}
    return Response(content=html, media_type="text/html; charset=utf-8", headers=headers)


@app.get("/health")
async def health() -> Dict[str, str]:
    logger.info("Health check requested")
    return {"status": "ok"}


@app.post("/api/v1/visit", response_model=VisitResponse)
async def visit_area(body: VisitRequest, user=Depends(get_current_user)):
    user_id, _ = user
    logger.info(f"Visit request: lat={body.lat}, lon={body.lon}, user_id={user_id}")

    conn = db_module.get_connection()

    lat, lon = float(body.lat), float(body.lon)
    H3_RESOLUTION = int(os.getenv("H3_RESOLUTION", "13"))
    logger.info(f"Visit quantization: H3_RESOLUTION={H3_RESOLUTION}")
    geokey = h3.latlng_to_cell(lat, lon, H3_RESOLUTION)

    added = db_module.insert_hexagon_if_new(conn, user_id=user_id, geokey=geokey)
    total = db_module.count_hexagons(conn, user_id=user_id)

    logger.info(f"Visit processed: added={added}, total_hexagons={total}, geokey={geokey}")
    return VisitResponse(
        added=1 if added else 0,
        stats={"total_hexagons": total},
    )


@app.get("/api/v1/hexagons", response_model=HexagonsResponse)
async def list_hexagons(user=Depends(get_current_user)):
    user_id, _ = user
    logger.info(f"Hexagons request: user_id={user_id}")

    conn = db_module.get_connection()
    hexagons = db_module.select_hexagons_by_user(conn, user_id=user_id)

    logger.info(f"Hexagons response: {len(hexagons)} hexagons returned")
    return HexagonsResponse(hexagons=hexagons)


# -------------------------
# Debug auth endpoints
# -------------------------

class AuthRequest(BaseModel):
    initData: str


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
        "no_auth_mode": NO_AUTH_MODE
    }
