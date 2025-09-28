import os
import json
import hmac
import hashlib
import urllib.parse
import logging
import sys
import time
import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
import h3
from starlette.middleware.sessions import SessionMiddleware
from pythonjsonlogger import jsonlogger

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # добавляем services в path
from common import db as db_module
from common import models
import cache
from redis.asyncio import Redis
import tracing


# Configure JSON logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Remove any existing handlers
for handler in logger.handlers[:]:
    logger.removeHandler(handler)

# Create JSON formatter
json_formatter = jsonlogger.JsonFormatter()

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


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Вычисляет расстояние между двумя точками на Земле в километрах.
    Использует формулу Haversine.

    Args:
        lat1, lon1: Координаты первой точки
        lat2, lon2: Координаты второй точки

    Returns:
        Расстояние в километрах
    """
    # Радиус Земли в километрах
    R = 6371.0

    # Преобразование в радианы
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Разницы координат
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    # Формула Haversine
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = R * c
    return distance


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


def _parse_bbox(bbox: str) -> Tuple[float, float, float, float]:
    try:
        min_lon_str, min_lat_str, max_lon_str, max_lat_str = bbox.split(",")
        min_lon, min_lat = float(min_lon_str), float(min_lat_str)
        max_lon, max_lat = float(max_lon_str), float(max_lat_str)
    except Exception:
        raise HTTPException(status_code=400, detail="bad bbox")

    if min_lon > max_lon or min_lat > max_lat:
        raise HTTPException(status_code=400, detail="bad bbox order")
    return min_lon, min_lat, max_lon, max_lat


def _progress_from_counts(
    visited_cells: int,
    total_cells: int,
    *,
    visited_weight: float = 0.0,
    total_weight: float = 0.0,
) -> models.ProgressBreakdown:
    percent_cells = 0.0
    percent_weight = 0.0

    if total_cells > 0:
        percent_cells = round((visited_cells / total_cells) * 100.0, 2)

    if total_weight > 0:
        percent_weight = round((visited_weight / total_weight) * 100.0, 2)

    return models.ProgressBreakdown(
        visited_cells=int(visited_cells),
        total_cells=int(total_cells),
        percent=percent_cells,
        percent_cells=percent_cells,
        percent_weight=percent_weight,
        visited_weight=float(visited_weight),
        total_weight=float(total_weight),
    )


def _parse_res_view(res_view: Optional[str], base_resolution: int) -> int:
    if not res_view:
        return base_resolution
    try:
        candidate = int(res_view.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="bad res_view")

    if candidate < 0:
        raise HTTPException(status_code=400, detail="bad res_view")
    if candidate > base_resolution:
        raise HTTPException(status_code=400, detail="res_view must be <= base resolution")
    return candidate


def _build_cells_payload(
    base_cells: List[Tuple[str, float]],
    visited: Set[str],
    base_resolution: int,
    target_resolution: int,
) -> List[models.DistrictCellResponse]:
    if target_resolution >= base_resolution:
        target_resolution = base_resolution

    if target_resolution == base_resolution:
        cells: List[models.DistrictCellResponse] = []
        for h3_index, coverage in base_cells:
            is_visited = h3_index in visited
            cells.append(
                models.DistrictCellResponse(
                    h3=h3_index,
                    coverage=round(float(coverage), 6),
                    visited=is_visited,
                    total_children=1,
                    visited_children=1 if is_visited else 0,
                    visited_fraction=1.0 if is_visited else 0.0,
                )
            )
        cells.sort(key=lambda c: c.h3)
        return cells

    aggregated: Dict[str, Dict[str, Any]] = {}
    for h3_index, coverage in base_cells:
        try:
            parent_h3 = h3.cell_to_parent(h3_index, target_resolution)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to compute parent for %s at res %s: %s", h3_index, target_resolution, exc)
            parent_h3 = h3_index

        bucket = aggregated.setdefault(
            parent_h3,
            {
                "coverage_sum": 0.0,
                "total_children": 0,
                "visited_children": 0,
            },
        )
        bucket["coverage_sum"] += float(coverage)
        bucket["total_children"] += 1
        if h3_index in visited:
            bucket["visited_children"] += 1

    cells: List[models.DistrictCellResponse] = []
    for parent_h3, bucket in aggregated.items():
        total_children = bucket["total_children"] or 1
        visited_children = bucket["visited_children"]
        coverage_avg = bucket["coverage_sum"] / total_children
        visited_fraction = visited_children / total_children
        cells.append(
            models.DistrictCellResponse(
                h3=parent_h3,
                coverage=round(min(1.0, coverage_avg), 6),
                visited=visited_children > 0,
                total_children=total_children,
                visited_children=visited_children,
                visited_fraction=round(visited_fraction, 6),
            )
        )

    cells.sort(key=lambda c: c.h3)
    return cells


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

    session_user = _get_user_from_session(request)
    if session_user:
        return session_user

    if DEBUG_AUTH_MODE:
        raise HTTPException(status_code=503, detail="Authentication via session only in debug auth mode")

    if not telegram_init:
        logger.warning("No X-Telegram-Init header provided")
        raise HTTPException(status_code=401, detail="missing initData")

    return _get_user_from_header(telegram_init)


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

app = FastAPI(title="City Fog Map API", version="0.1.0", lifespan=lifespan)

# Add Prometheus metrics
from prometheus_fastapi_instrumentator import Instrumentator
instrumentator = Instrumentator()
instrumentator.instrument(app)
instrumentator.expose(app)

# Static frontend at /webapp
webapp_dir = "/webapp"
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


@app.post("/api/v1/visit", response_model=models.VisitResponse)
async def visit_area(
    body: models.VisitRequest,
    user=Depends(get_current_user),
    redis_client: Optional[Redis] = Depends(cache.get_redis),
):
    # DEBUG_AUTH_MODE check is now in get_current_user
    user_id, _ = user
    logger.info(f"Visit request: lat={body.lat}, lon={body.lon}, user_id={user_id}")

    # Проверка скорости и телепортов
    if redis_client:
        try:
            last_visit_key = f"user:{user_id}:last_visit"
            last_visit_data = await redis_client.get(last_visit_key)

            if last_visit_data:
                # Парсим данные последнего визита: timestamp,lat,lon
                last_visit_str = last_visit_data.decode('utf-8')
                last_timestamp_str, last_lat_str, last_lon_str = last_visit_str.split(',')
                last_timestamp = float(last_timestamp_str)
                last_lat = float(last_lat_str)
                last_lon = float(last_lon_str)

                current_timestamp = time.time()
                time_diff_seconds = current_timestamp - last_timestamp

                if time_diff_seconds > 0:  # Избегаем деления на ноль
                    distance_km = calculate_distance(last_lat, last_lon, body.lat, body.lon)
                    speed_kmh = (distance_km / time_diff_seconds) * 3600  # км/ч

                    logger.info(f"Speed check: distance={distance_km:.3f}km, time_diff={time_diff_seconds:.1f}s, speed={speed_kmh:.1f}km/h")

                    # Проверка условий: (дистанция > 2 км и время < 10 сек) ИЛИ скорость > 150 км/ч
                    if distance_km > 2.0 and time_diff_seconds < 10.0:
                        logger.warning(f"Visit rejected: teleport detected (distance={distance_km:.3f}km in {time_diff_seconds:.1f}s) for user {user_id}")
                        raise HTTPException(status_code=400, detail="Visit rejected: teleport detected")

                    if speed_kmh > 150.0:
                        logger.warning(f"Visit rejected: excessive speed {speed_kmh:.1f} km/h for user {user_id}")
                        raise HTTPException(status_code=400, detail="Visit rejected: excessive speed detected")

        except Exception as e:
            logger.error(f"Error during speed check for user {user_id}: {e}")
            # В случае ошибки проверки скорости разрешаем визит, но логируем ошибку

    conn = db_module.get_connection()

    lat, lon = float(body.lat), float(body.lon)

    geokey = h3.latlng_to_cell(lat, lon, db_module.BASE_VISIT_RESOLUTION)

    district_row = db_module.select_district_for_cell(conn, geokey)
    if not district_row:
        logger.info(f"Visit ignored: no district for geokey={geokey}")
        stats_dict = db_module.fetch_user_stats(conn, user_id=user_id, district_id=None, okrug_id=None)
        stats = models.VisitStats(
            total_circles=stats_dict["total_circles"],
            district=None,
            okrug=None,
        )

        # Инвалидируем кэш статистики пользователя
        if redis_client:
            try:
                cache_key = f"user:{user_id}:stats_summary"
                await redis_client.delete(cache_key)
                logger.info(f"Invalidated cache for user {user_id} stats summary")
            except Exception as e:
                logger.warning(f"Error invalidating Redis cache: {e}")

        return models.VisitResponse(
            added=0,
            circle=models.Circle(lat=lat, lon=lon),
            stats=stats,
        )

    district_id, coverage = district_row
    okrug_id = db_module.select_district_parent(conn, district_id)

    added = db_module.record_visit_and_increment_stats(
        conn,
        user_id=user_id,
        h3_index=geokey,
        district_id=district_id,
        coverage=coverage,
        okrug_id=okrug_id,
    )

    stats_dict = db_module.fetch_user_stats(
        conn,
        user_id=user_id,
        district_id=district_id,
        okrug_id=okrug_id,
    )
    stats = models.VisitStats(
        total_circles=stats_dict["total_circles"],
        district=models.RegionStats(**stats_dict["district"]) if stats_dict.get("district") else None,
        okrug=models.RegionStats(**stats_dict["okrug"]) if stats_dict.get("okrug") else None,
    )

    # Инвалидируем кэш статистики пользователя
    if redis_client:
        try:
            cache_key = f"user:{user_id}:stats_summary"
            await redis_client.delete(cache_key)
            logger.info(f"Invalidated cache for user {user_id} stats summary")
        except Exception as e:
            logger.warning(f"Error invalidating Redis cache: {e}")

    # Обновляем данные последнего визита в Redis
    if redis_client:
        try:
            last_visit_key = f"user:{user_id}:last_visit"
            current_timestamp = time.time()
            visit_data = f"{current_timestamp},{lat},{lon}"
            await redis_client.set(last_visit_key, visit_data)
            logger.info(f"Updated last visit data for user {user_id}: {visit_data}")
        except Exception as e:
            logger.error(f"Error updating last visit data in Redis for user {user_id}: {e}")

    logger.info(
        f"Visit processed: added={added}, district_id={district_id}, okrug_id={okrug_id}, geokey={geokey}, coverage={coverage:.3f}"
    )
    return models.VisitResponse(
        added=1 if added else 0,
        circle=models.Circle(lat=lat, lon=lon),
        stats=stats,
    )


@app.get("/api/v1/circles", response_model=models.CirclesResponse)
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

    logger.info(f"Parsed bbox: min_lon={min_lon}, min_lat={min_lat}, max_lon={max_lon}, max_lat={max_lat}")

    conn = db_module.get_connection()

    # Debug: get all user hexagons first
    all_user_hexagons = db_module.select_user_hexes(conn, user_id)
    logger.info(f"User {user_id} has {len(all_user_hexagons)} total hexagons")

    hexagons = db_module.select_user_hexes_in_bbox(
        conn,
        user_id=user_id,
        min_lat=min_lat,
        min_lon=min_lon,
        max_lat=max_lat,
        max_lon=max_lon,
    )

    logger.info(f"Circles response: {len(hexagons)} hexagons returned in bbox")
    if len(hexagons) > 0:
        logger.info(f"Sample hexagons: {hexagons[:3]}")
    return models.CirclesResponse(hexagons=hexagons)


@app.delete("/api/v1/circle")
async def delete_circle(body: models.DeleteCircleRequest, user=Depends(get_current_user)):
    user_id, _ = user
    conn = db_module.get_connection()
    deleted = db_module.delete_visit_by_hex(conn, user_id=user_id, h3_index=body.geokey)
    return {"deleted": int(deleted)}


# -------------------------
# Debug auth endpoints
# -------------------------

@app.post("/api/auth")
async def debug_auth(body: models.AuthRequest, request: Request):
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


@app.get("/api/user/{user_id}", response_model=models.UserInfo)
async def get_user(user_id: int):
    """Get user information by internal user ID"""
    conn = db_module.get_connection()
    user_data = db_module.get_user_by_id(conn, user_id)
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    tg_id, username = user_data
    return models.UserInfo(id=user_id, tg_id=tg_id, username=username)


@app.post("/api/authenticate")
async def authenticate(user=Depends(get_current_user)) -> Dict[str, Any]:
    """Authenticate user and return user info with JWT-like token (simplified)"""
    user_id, username = user
    return {
        "user_id": user_id,
        "username": username,
        "authenticated": True
    }


@app.get("/api/ping")
async def ping():
    return {"ok": True}


@app.get("/api/v1/debug-mode")
async def debug_mode():
    """Return debug mode status for frontend"""
    return {
        "debug_auth_mode": DEBUG_AUTH_MODE,
        "no_auth_mode": NO_AUTH_MODE,
        "base_visit_resolution": db_module.BASE_VISIT_RESOLUTION,
    }


# Dev utility: clear entire database (allowed only in debug/no-auth)
@app.post("/api/v1/dev/clear-db")
async def dev_clear_db():
    if not (DEBUG_AUTH_MODE or NO_AUTH_MODE):
        raise HTTPException(status_code=403, detail="forbidden")
    conn = db_module.get_connection()
    cleared_circles, cleared_users = db_module.clear_all(conn)
    logger.warning(f"DEV clear-db executed: circles={cleared_circles}, users={cleared_users}")
    return {"cleared_circles": int(cleared_circles), "cleared_users": int(cleared_users)}


@app.get(
    "/api/v1/districts",
    response_model=List[models.DistrictFeatureResponse],
)
async def list_districts(
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat"),
    level: Literal["okrug", "district"] = Query(
        "district", description="Administrative level to return"
    ),
    user=Depends(get_current_user),
):
    user_id, _ = user
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
    conn = db_module.get_connection()
    rows = db_module.fetch_districts_in_bbox(
        conn,
        user_id=user_id,
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        level=level,
    )

    features: List[models.DistrictFeatureResponse] = []
    for row in rows:
        bbox_values: Optional[List[float]] = None
        if (
            row["bbox_min_lon"] is not None
            and row["bbox_min_lat"] is not None
            and row["bbox_max_lon"] is not None
            and row["bbox_max_lat"] is not None
        ):
            bbox_values = [
                float(row["bbox_min_lon"]),
                float(row["bbox_min_lat"]),
                float(row["bbox_max_lon"]),
                float(row["bbox_max_lat"]),
            ]

        geom_raw = row["geom_geojson"]
        geom_payload: Dict[str, Any]
        try:
            geom_payload = json.loads(geom_raw) if geom_raw else {}
        except json.JSONDecodeError:
            logger.warning("Failed to decode geometry for district %s", row["id"])
            geom_payload = {}

        total_cells = int(row["total_cells"]) if row["total_cells"] is not None else 0
        total_weight = float(row["total_weight"]) if row["total_weight"] is not None else 0.0
        visited_cells = (
            int(row["user_visited_cells"]) if row["user_visited_cells"] is not None else 0
        )
        visited_weight = (
            float(row["user_visited_weight"]) if row["user_visited_weight"] is not None else 0.0
        )

        progress = _progress_from_counts(
            visited_cells=visited_cells,
            total_cells=total_cells,
            visited_weight=visited_weight,
            total_weight=total_weight,
        )

        parent_id = row["parent_id"]
        features.append(
            models.DistrictFeatureResponse(
                id=int(row["id"]),
                name=str(row["name_ru"]),
                level=str(row["level"]),
                parent_id=int(parent_id) if parent_id is not None else None,
                bbox=bbox_values,
                geom=geom_payload,
                progress=progress,
            )
        )

    return features


@app.get(
    "/api/v1/districts/all",
    response_model=List[models.DistrictFeatureResponse],
)
async def list_all_districts(user=Depends(get_current_user)):
    user_id, _ = user
    conn = db_module.get_connection()
    rows = db_module.fetch_all_districts_with_user_progress(conn, user_id=user_id)

    features: List[models.DistrictFeatureResponse] = []
    for row in rows:
        bbox_values: Optional[List[float]] = None
        if (
            row["bbox_min_lon"] is not None
            and row["bbox_min_lat"] is not None
            and row["bbox_max_lon"] is not None
            and row["bbox_max_lat"] is not None
        ):
            bbox_values = [
                float(row["bbox_min_lon"]),
                float(row["bbox_min_lat"]),
                float(row["bbox_max_lon"]),
                float(row["bbox_max_lat"]),
            ]

        geom_raw = row["geom_geojson"]
        geom_payload: Dict[str, Any]
        try:
            geom_payload = json.loads(geom_raw) if geom_raw else {}
        except json.JSONDecodeError:
            logger.warning("Failed to decode geometry for district %s", row["id"])
            geom_payload = {}

        total_cells = int(row["total_cells"]) if row["total_cells"] is not None else 0
        total_weight = float(row["total_weight"]) if row["total_weight"] is not None else 0.0
        visited_cells = (
            int(row["user_visited_cells"]) if row["user_visited_cells"] is not None else 0
        )
        visited_weight = (
            float(row["user_visited_weight"]) if row["user_visited_weight"] is not None else 0.0
        )

        progress = _progress_from_counts(
            visited_cells=visited_cells,
            total_cells=total_cells,
            visited_weight=visited_weight,
            total_weight=total_weight,
        )

        parent_id = row["parent_id"]
        features.append(
            models.DistrictFeatureResponse(
                id=int(row["id"]),
                name=str(row["name_ru"]),
                level=str(row["level"]),
                parent_id=int(parent_id) if parent_id is not None else None,
                bbox=bbox_values,
                geom=geom_payload,
                progress=progress,
            )
        )

    return features


@app.get(
    "/api/v1/district/{district_id}/cells",
    response_model=models.DistrictCellsResponse,
)
async def get_district_cells(
    district_id: int,
    res_view: Optional[str] = Query(
        None, description="Optional H3 resolution to aggregate to (<= base)"
    ),
    user=Depends(get_current_user),
):
    user_id, _ = user
    conn = db_module.get_connection()
    district_row = db_module.get_district_by_id(conn, district_id)
    if not district_row:
        raise HTTPException(status_code=404, detail="district not found")

    base_cells = db_module.fetch_district_cells(conn, district_id)
    if base_cells:
        base_resolution = h3.get_resolution(base_cells[0][0])
    else:
        base_resolution = db_module.BASE_VISIT_RESOLUTION

    visited_cells = set(
        db_module.fetch_user_visited_cells_for_district(
            conn, user_id=user_id, district_id=district_id
        )
    )

    target_resolution = _parse_res_view(res_view, base_resolution)
    cells_payload = _build_cells_payload(
        base_cells,
        visited_cells,
        base_resolution,
        target_resolution,
    )

    return models.DistrictCellsResponse(
        district_id=district_id,
        resolution=target_resolution,
        base_resolution=base_resolution,
        cells=cells_payload,
    )


@app.post("/api/v1/district/{district_id}/reveal")
async def reveal_district(
    district_id: int,
    payload: Dict[str, Any],
    user=Depends(get_current_user),
):
    user_id, _ = user
    if not (DEBUG_AUTH_MODE or NO_AUTH_MODE):
        raise HTTPException(status_code=403, detail="forbidden")

    conn = db_module.get_connection()
    district_row = db_module.get_district_by_id(conn, district_id)
    if not district_row:
        raise HTTPException(status_code=404, detail="district not found")

    base_cells = db_module.fetch_district_cells(conn, district_id)
    if not base_cells:
        return {"new_hexagons": []}

    okrug_id = db_module.select_district_parent(conn, district_id)

    requested_cells = set()
    if isinstance(payload, dict):
        cells = payload.get("cells")
        if isinstance(cells, list):
            requested_cells = {str(cell) for cell in cells}

    new_hexagons: List[str] = []
    already_visited = set(
        db_module.fetch_user_visited_cells_for_district(
            conn, user_id=user_id, district_id=district_id
        )
    )

    for h3_index, coverage in base_cells:
        if requested_cells and h3_index not in requested_cells:
            continue
        added = db_module.record_visit_and_increment_stats(
            conn,
            user_id=user_id,
            h3_index=h3_index,
            district_id=district_id,
            coverage=coverage,
            okrug_id=okrug_id,
        )
        if added:
            new_hexagons.append(h3_index)

    return {"new_hexagons": new_hexagons}


@app.get("/api/v1/stats/summary", response_model=models.StatsSummaryResponse)
async def get_stats_summary(
    user=Depends(get_current_user),
    redis_client: Optional[Redis] = Depends(cache.get_redis),
):
    user_id, _ = user

    # Формируем уникальный ключ для кэша
    cache_key = f"user:{user_id}:stats_summary"

    # Проверяем кэш
    if redis_client:
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                logger.info(f"Stats summary cache hit for user {user_id}")
                # Десериализуем из JSON
                cached_response = models.StatsSummaryResponse.model_validate_json(cached_data)
                return cached_response
        except Exception as e:
            logger.warning(f"Error reading from Redis cache: {e}")

    logger.info(f"Stats summary cache miss for user {user_id}")

    # Cache miss - выполняем запрос к базе данных
    conn = db_module.get_connection()

    totals = db_module.fetch_user_total_progress(conn, user_id=user_id)
    total_progress = _progress_from_counts(
        visited_cells=int(totals["visited_cells"]),
        total_cells=int(totals["total_cells"]),
        visited_weight=float(totals["visited_weight"]),
        total_weight=float(totals["total_weight"]),
    )

    okrug_rows = db_module.fetch_user_okrug_progress(conn, user_id=user_id)
    okrugs: List[models.OkrugSummaryEntry] = []
    for row in okrug_rows:
        okrugs.append(
            models.OkrugSummaryEntry(
                id=int(row["id"]),
                name=str(row["name_ru"]),
                parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
                progress=_progress_from_counts(
                    visited_cells=int(row["visited_cells"]) if row["visited_cells"] is not None else 0,
                    total_cells=int(row["total_cells"]) if row["total_cells"] is not None else 0,
                    visited_weight=float(row["visited_weight"]) if row["visited_weight"] is not None else 0.0,
                    total_weight=float(row["total_weight"]) if row["total_weight"] is not None else 0.0,
                ),
            )
        )

    bottom_rows = db_module.fetch_user_bottom_districts(conn, user_id=user_id, limit=3)
    bottom_districts: List[models.DistrictSummaryEntry] = []
    for row in bottom_rows:
        bottom_districts.append(
            models.DistrictSummaryEntry(
                id=int(row["id"]),
                name=str(row["name_ru"]),
                parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
                parent_name=str(row["parent_name"]) if row["parent_name"] is not None else None,
                progress=_progress_from_counts(
                    visited_cells=int(row["visited_cells"]) if row["visited_cells"] is not None else 0,
                    total_cells=int(row["total_cells"]) if row["total_cells"] is not None else 0,
                    visited_weight=float(row["visited_weight"]) if row["visited_weight"] is not None else 0.0,
                    total_weight=float(row["total_weight"]) if row["total_weight"] is not None else 0.0,
                ),
            )
        )

    response = models.StatsSummaryResponse(
        total=total_progress,
        okrugs=okrugs,
        bottom_districts=bottom_districts,
    )

    # Сохраняем в кэш с TTL 3600 секунд
    if redis_client:
        try:
            await redis_client.setex(cache_key, 3600, response.model_dump_json())
            logger.info(f"Cached stats summary for user {user_id}")
        except Exception as e:
            logger.warning(f"Error writing to Redis cache: {e}")

    return response


@app.get("/api/v1/leaderboard", response_model=models.LeaderboardResponse)
async def get_leaderboard(
    level: Literal["district", "okrug"] = Query(
        "district", description="Aggregation level"),
    period: Literal["week", "season"] = Query(
        "week", description="Leaderboard period"),
    limit: int = Query(10, ge=1, le=100, description="Number of entries to return"),
    user=Depends(get_current_user),
    redis_client: Optional[Redis] = Depends(cache.get_redis),
):
    _ = user  # currently unused but validates auth

    # Формируем уникальный ключ для кэша
    cache_key = f"leaderboard:{level}:{period}:{limit}"

    # Проверяем кэш
    if redis_client:
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                logger.info(f"Leaderboard cache hit for key: {cache_key}")
                # Десериализуем из JSON
                cached_response = models.LeaderboardResponse.model_validate_json(cached_data)
                return cached_response
        except Exception as e:
            logger.warning(f"Error reading from Redis cache: {e}")

    logger.info(f"Leaderboard cache miss for key: {cache_key}")

    # Cache miss - выполняем запрос к базе данных
    conn = db_module.get_connection()
    total_cells, total_weight = db_module.get_total_cells_and_weight(conn, level=level)

    if total_cells <= 0 and total_weight <= 0:
        response = models.LeaderboardResponse(
            level=level,
            period=period,
            generated_at=datetime.now(timezone.utc),
            entries=[],
        )
    else:
        rows = db_module.fetch_leaderboard(
            conn,
            level=level,
            period=period,
            limit=limit,
        )

        entries: List[models.LeaderboardEntry] = []
        for idx, row in enumerate(rows, start=1):
            visited_cells = int(row["visited_cells"] or 0)
            visited_weight = float(row["visited_weight"] or 0.0)

            percent_cells = 0.0
            if total_cells > 0:
                percent_cells = round((visited_cells / total_cells) * 100.0, 2)

            percent_weight = 0.0
            if total_weight > 0:
                percent_weight = round((visited_weight / total_weight) * 100.0, 2)

            entries.append(
                models.LeaderboardEntry(
                    rank=idx,
                    user_id=int(row["user_id"]),
                    username=row["username"],
                    visited_cells=visited_cells,
                    visited_weight=visited_weight,
                    percent_cells=percent_cells,
                    percent_weight=percent_weight,
                )
            )

        response = models.LeaderboardResponse(
            level=level,
            period=period,
            generated_at=datetime.now(timezone.utc),
            entries=entries,
        )

    # Сохраняем в кэш
    if redis_client:
        try:
            await redis_client.setex(cache_key, 300, response.model_dump_json())
            logger.info(f"Cached leaderboard response for key: {cache_key}")
        except Exception as e:
            logger.warning(f"Error writing to Redis cache: {e}")

    return response


