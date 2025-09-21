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
from fastapi.responses import JSONResponse, RedirectResponse
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


class Circle(BaseModel):
    lat: float
    lon: float
    radius_m: int = 100


class VisitResponse(BaseModel):
    added: int
    circle: Circle
    stats: Dict[str, int]


class CirclesResponse(BaseModel):
    circles: List[Circle]


async def get_current_user(request: Request, telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init")) -> Tuple[int, Optional[str]]:
    logger.info("Authenticating user")

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
app.mount("/webapp", StaticFiles(directory=webapp_dir, html=True), name="webapp")

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
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            f"HTTP RES {request.method} {path_and_qs} -> {getattr(response, 'status_code', 'n/a')} in {duration_ms:.1f} ms"
        )


@app.on_event("startup")
def on_startup() -> None:
    logger.info("Database initialization...")
    conn = db_module.get_connection()
    db_module.init_db(conn)
    logger.info("Database initialized successfully")


@app.get("/health")
async def health() -> Dict[str, str]:
    logger.info("Health check requested")
    return {"status": "ok"}


@app.post("/api/v1/visit", response_model=VisitResponse)
async def visit_area(body: VisitRequest, user=Depends(get_current_user)):
    # DEBUG_AUTH_MODE check is now in get_current_user
    user_id, _ = user
    logger.info(f"Visit request: lat={body.lat}, lon={body.lon}, user_id={user_id}")

    conn = db_module.get_connection()

    lat, lon = float(body.lat), float(body.lon)
    # H3 resolution approximately ~100m per hexagon
    resolution = 11
    geokey = h3.geo_to_h3(lat, lon, resolution)

    added = db_module.insert_circle_if_new(conn, user_id=user_id, geokey=geokey, lat=lat, lon=lon)
    total = db_module.count_circles(conn, user_id=user_id)

    logger.info(f"Visit processed: added={added}, total_circles={total}, geokey={geokey}")
    return VisitResponse(
        added=1 if added else 0,
        circle=Circle(lat=lat, lon=lon, radius_m=100),
        stats={"total_circles": total},
    )


@app.get("/api/v1/circles", response_model=CirclesResponse)
async def list_circles(bbox: str, user=Depends(get_current_user)):
    # DEBUG_AUTH_MODE check is now in get_current_user
    user_id, _ = user
    logger.info(f"Circles request: bbox={bbox}, user_id={user_id}")

    try:
        min_lon_str, min_lat_str, max_lon_str, max_lat_str = bbox.split(",")
        min_lon, min_lat = float(min_lon_str), float(min_lat_str)
        max_lon, max_lat = float(max_lon_str), float(max_lat_str)
    except Exception as e:
        logger.error(f"Invalid bbox format: {bbox}, error: {e}")
        raise HTTPException(status_code=400, detail="bad bbox")

    conn = db_module.get_connection()
    rows = db_module.select_circles_in_bbox(conn, user_id=user_id, min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    items = [Circle(lat=r[0], lon=r[1], radius_m=int(r[2])) for r in rows]

    logger.info(f"Circles response: {len(items)} circles returned")
    return CirclesResponse(circles=items)


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


