import json
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
import h3
import psycopg2.extras

from services.common import database as db_module
from common import models
import cache
from redis.asyncio import Redis

# Configure JSON logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["districts"])


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
    visited: set[str],
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


@router.get(
    "/districts",
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


@router.get(
    "/districts/all",
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


@router.get(
    "/district/{district_id}/cells",
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


@router.post("/district/{district_id}/reveal")
async def reveal_district(
    district_id: int,
    payload: Dict[str, Any],
    user=Depends(get_current_user),
):
    from services.monolith.main import DEBUG_AUTH_MODE, NO_AUTH_MODE

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
