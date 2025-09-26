import os
import json
import hmac
import hashlib
import logging
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
import h3
from redis.asyncio import Redis
from pythonjsonlogger import jsonlogger

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

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import db as db_module
import cache

# Import tracing from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import tracing

# Setup OpenTelemetry tracing
tracing.setup_tracing("geo-service")

# Telegram Bot Token for authentication
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN not set - Telegram auth will not work")

# Debug mode toggle
DEBUG_AUTH_MODE = os.getenv("DEBUG_AUTH_MODE", "0") == "1"
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


class Circle(BaseModel):
    lat: float
    lon: float


class RegionStats(BaseModel):
    id: int
    visited_cells: int
    visited_weight: float


class ProgressBreakdown(BaseModel):
    visited_cells: int
    total_cells: int
    percent: float
    percent_cells: float = 0.0
    percent_weight: float = 0.0
    visited_weight: float = 0.0
    total_weight: float = 0.0


class DistrictFeatureResponse(BaseModel):
    id: int
    name: str
    level: Literal["okrug", "district"]
    parent_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    geom: Dict[str, Any]
    progress: ProgressBreakdown


class DistrictCellResponse(BaseModel):
    h3: str
    coverage: float
    visited: bool
    total_children: Optional[int] = None
    visited_children: Optional[int] = None
    visited_fraction: Optional[float] = None


class DistrictCellsResponse(BaseModel):
    district_id: int
    resolution: int
    base_resolution: int
    cells: List[DistrictCellResponse]


class OkrugSummaryEntry(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    progress: ProgressBreakdown


class DistrictSummaryEntry(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    parent_name: Optional[str] = None
    progress: ProgressBreakdown


class StatsSummaryResponse(BaseModel):
    total: ProgressBreakdown
    okrugs: List[OkrugSummaryEntry]
    bottom_districts: List[DistrictSummaryEntry]


class LeaderboardEntry(BaseModel):
    rank: int
    user_id: int
    username: Optional[str]
    visited_cells: int
    visited_weight: float
    percent_cells: float
    percent_weight: float


class LeaderboardResponse(BaseModel):
    level: Literal["district", "okrug"]
    period: Literal["week", "season"]
    generated_at: datetime
    entries: List[LeaderboardEntry]


class CirclesResponse(BaseModel):
    hexagons: List[str]


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
) -> ProgressBreakdown:
    percent_cells = 0.0
    percent_weight = 0.0

    if total_cells > 0:
        percent_cells = round((visited_cells / total_cells) * 100.0, 2)

    if total_weight > 0:
        percent_weight = round((visited_weight / total_weight) * 100.0, 2)

    return ProgressBreakdown(
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
) -> List[DistrictCellResponse]:
    if target_resolution >= base_resolution:
        target_resolution = base_resolution

    if target_resolution == base_resolution:
        cells: List[DistrictCellResponse] = []
        for h3_index, coverage in base_cells:
            is_visited = h3_index in visited
            cells.append(
                DistrictCellResponse(
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

    cells: List[DistrictCellResponse] = []
    for parent_h3, bucket in aggregated.items():
        total_children = bucket["total_children"] or 1
        visited_children = bucket["visited_children"]
        coverage_avg = bucket["coverage_sum"] / total_children
        visited_fraction = visited_children / total_children
        cells.append(
            DistrictCellResponse(
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


def get_current_user(
    request: Request,
    telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init"),
) -> int:
    logger.info(f"Authenticating user, NO_AUTH_MODE={NO_AUTH_MODE}")

    if NO_AUTH_MODE:
        logger.warning("NO_AUTH_MODE enabled: bypassing auth, using local user")
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="local")
        return user_id

    session_user = _get_user_from_session(request)
    if session_user:
        user_id, _ = session_user
        return user_id

    if DEBUG_AUTH_MODE:
        raise HTTPException(status_code=503, detail="Authentication via session only in debug auth mode")

    if not telegram_init:
        logger.warning("No X-Telegram-Init header provided")
        raise HTTPException(status_code=401, detail="missing initData")

    user_id, _ = _get_user_from_header(telegram_init)
    return user_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Geo Service API starting up...")

    # Initialize Redis
    await cache.init_redis_pool()

    # Initialize database
    logger.info("Database initialization...")
    conn = db_module.get_connection()
    db_module.init_db(conn)
    logger.info("Database initialized successfully")

    yield

    # Shutdown
    logger.info("🛑 Geo Service API shutting down...")

    # Close Redis connection
    await cache.close_redis_pool()


app = FastAPI(title="Geo Service API", version="0.1.0", lifespan=lifespan)

# Add Prometheus metrics
from prometheus_fastapi_instrumentator import Instrumentator
instrumentator = Instrumentator()
instrumentator.instrument(app)
instrumentator.expose(app)

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


@app.get(
    "/api/v1/districts",
    response_model=List[DistrictFeatureResponse],
)
async def list_districts(
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat"),
    level: Literal["okrug", "district"] = Query(
        "district", description="Administrative level to return"
    ),
    user_id: int = Depends(get_current_user),
):
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

    features: List[DistrictFeatureResponse] = []
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
            DistrictFeatureResponse(
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
    response_model=List[DistrictFeatureResponse],
)
async def list_all_districts(user_id: int = Depends(get_current_user)):
    conn = db_module.get_connection()
    rows = db_module.fetch_all_districts_with_user_progress(conn, user_id=user_id)

    features: List[DistrictFeatureResponse] = []
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
            DistrictFeatureResponse(
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
    response_model=DistrictCellsResponse,
)
async def get_district_cells(
    district_id: int,
    res_view: Optional[str] = Query(
        None, description="Optional H3 resolution to aggregate to (<= base)"
    ),
    user_id: int = Depends(get_current_user),
):
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

    return DistrictCellsResponse(
        district_id=district_id,
        resolution=target_resolution,
        base_resolution=base_resolution,
        cells=cells_payload,
    )


@app.get("/api/v1/circles")
async def list_circles(bbox: str, user_id: int = Depends(get_current_user)):
    logger.info(f"Circles request: bbox={bbox}, user_id={user_id}")

    try:
        min_lon_str, min_lat_str, max_lon_str, max_lat_str = bbox.split(",")
        min_lon, min_lat = float(min_lon_str), float(min_lat_str)
        max_lon, max_lat = float(max_lon_str), float(max_lat_str)
    except Exception as e:
        logger.error(f"Invalid bbox format: {bbox}, error: {e}")
        raise HTTPException(status_code=400, detail="bad bbox")

    conn = db_module.get_connection()
    hexagons = db_module.select_user_hexes_in_bbox(
        conn,
        user_id=user_id,
        min_lat=min_lat,
        min_lon=min_lon,
        max_lat=max_lat,
        max_lon=max_lon,
    )

    logger.info(f"Circles response: {len(hexagons)} hexagons returned")
    return Response(content=' '.join(hexagons), media_type="text/plain")


@app.get("/api/v1/stats/summary", response_model=StatsSummaryResponse)
async def get_stats_summary(
    user_id: int = Depends(get_current_user),
    redis_client: Optional[Redis] = Depends(cache.get_redis),
):
    # Формируем уникальный ключ для кэша
    cache_key = f"user:{user_id}:stats_summary"

    # Проверяем кэш
    if redis_client:
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                logger.info(f"Stats summary cache hit for user {user_id}")
                # Десериализуем из JSON
                cached_response = StatsSummaryResponse.model_validate_json(cached_data)
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
    okrugs: List[OkrugSummaryEntry] = []
    for row in okrug_rows:
        okrugs.append(
            OkrugSummaryEntry(
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
    bottom_districts: List[DistrictSummaryEntry] = []
    for row in bottom_rows:
        bottom_districts.append(
            DistrictSummaryEntry(
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

    response = StatsSummaryResponse(
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


@app.get("/api/v1/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    level: Literal["district", "okrug"] = Query(
        "district", description="Aggregation level"),
    period: Literal["week", "season"] = Query(
        "week", description="Leaderboard period"),
    limit: int = Query(10, ge=1, le=100, description="Number of entries to return"),
    user_id: int = Depends(get_current_user),
    redis_client: Optional[Redis] = Depends(cache.get_redis),
):
    _ = user_id  # currently unused but validates auth

    # Формируем уникальный ключ для кэша
    cache_key = f"leaderboard:{level}:{period}:{limit}"

    # Проверяем кэш
    if redis_client:
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                logger.info(f"Leaderboard cache hit for key: {cache_key}")
                # Десериализуем из JSON
                cached_response = LeaderboardResponse.model_validate_json(cached_data)
                return cached_response
        except Exception as e:
            logger.warning(f"Error reading from Redis cache: {e}")

    logger.info(f"Leaderboard cache miss for key: {cache_key}")

    # Cache miss - выполняем запрос к базе данных
    conn = db_module.get_connection()
    total_cells, total_weight = db_module.get_total_cells_and_weight(conn, level=level)

    if total_cells <= 0 and total_weight <= 0:
        response = LeaderboardResponse(
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

        entries: List[LeaderboardEntry] = []
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
                LeaderboardEntry(
                    rank=idx,
                    user_id=int(row["user_id"]),
                    username=row["username"],
                    visited_cells=visited_cells,
                    visited_weight=visited_weight,
                    percent_cells=percent_cells,
                    percent_weight=percent_weight,
                )
            )

        response = LeaderboardResponse(
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
