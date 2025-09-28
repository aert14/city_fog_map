import math
import time
import logging
from typing import Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, Request
import h3
from redis.asyncio import Redis

from services.common import database as db_module
from common import models
import cache
import queue

# Configure JSON logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["visits"])


def calculate_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
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
    a = (math.sin(dlat / 2)**2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = R * c
    return distance


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


def _get_user_from_session(
    request: Request
) -> Optional[Tuple[int, Optional[str]]]:
    if request.session.get("tg_authenticated") and request.session.get("tg_user_id"):
        try:
            tg_id = int(request.session["tg_user_id"])
            tg_user = request.session.get("tg_user", {})
            username = (tg_user.get("username")
                        if isinstance(tg_user, dict) else None)

            conn = db_module.get_connection()
            user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
            logger.info(
                "User authenticated via session: user_id=%s, tg_id=%s, "
                "username=%s",
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
    from services.monolith.main import TELEGRAM_BOT_TOKEN

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: TELEGRAM_BOT_TOKEN not set"
        )

    result = _verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        raise HTTPException(
            status_code=401,
            detail=result.get("reason", "bad initData")
        )

    payload = result["payload"]
    user_raw = payload.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="no user in initData")

    try:
        import json
        user_obj = json.loads(user_raw)
        tg_id = int(user_obj["id"])
        username = user_obj.get("username")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="bad user json") from exc

    conn = db_module.get_connection()
    user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
    logger.info(
        "User authenticated via header: user_id=%s, tg_id=%s, "
        "username=%s",
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

    logger.info("Verifying initData: %s chars", len(raw_init_data))
    data = dict(urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True))
    recv_hash = data.pop("hash", None)
    if not recv_hash:
        logger.warning("Missing hash in initData")
        return {"ok": False, "reason": "missing hash"}

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    exp_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(exp_hash, recv_hash):
        logger.warning("Hash mismatch: expected %s..., got %s...",
                       exp_hash[:16], recv_hash[:16])
        return {"ok": False, "reason": "hash mismatch"}

    try:
        import datetime
        from datetime import timezone, timedelta
        auth_ts = int(data.get("auth_date", "0"))
    except ValueError:
        logger.warning("Invalid auth_date format: %s", data.get('auth_date'))
        return {"ok": False, "reason": "bad auth_date"}
    if auth_ts <= 0:
        logger.warning("Empty auth_date: %s", auth_ts)
        return {"ok": False, "reason": "empty auth_date"}
    if datetime.datetime.now(timezone.utc) - datetime.datetime.fromtimestamp(auth_ts, tz=timezone.utc) > timedelta(seconds=max_age_sec):
        logger.warning("Stale auth_date: %s", auth_ts)
        return {"ok": False, "reason": "stale auth_date"}

    # Do not parse user JSON here to avoid side effects; just log payload keys
    logger.info("InitData verified successfully; payload keys: %s",
                sorted(list(data.keys())))
    return {"ok": True, "payload": data}


async def get_current_user(
    request: Request,
    telegram_init: Optional[str] = None,  # Упрощено для совместимости
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


@router.post("/visit", response_model=models.VisitResponse)
async def visit_area(
    body: models.VisitRequest,
    user=Depends(get_current_user),
    redis_client: Optional[Redis] = Depends(cache.get_redis),
):
    """Record a visit to a geographic location.

    Records that the authenticated user has visited a specific latitude/longitude.
    Performs speed validation to prevent cheating (teleports and excessive speeds).
    Updates user progress statistics and publishes visit events to message queue.

    Args:
        body: Visit request containing latitude and longitude coordinates

    Returns:
        Visit response with success status, circle coordinates, and updated statistics.
    """
    # DEBUG_AUTH_MODE check is now in get_current_user
    user_id, _ = user
    logger.info("Visit request: lat=%s, lon=%s, user_id=%s",
                body.lat, body.lon, user_id)

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

                    logger.info("Speed check: distance=%.3fkm, time_diff=%.1fs, speed=%.1fkm/h",
                                distance_km, time_diff_seconds, speed_kmh)

                    # Проверка условий: (дистанция > 2 км и время < 10 сек) ИЛИ скорость > 150 км/ч
                    if distance_km > 2.0 and time_diff_seconds < 10.0:
                        logger.warning("Visit rejected: teleport detected (distance=%.3fkm in %.1fs) for user %s",
                                       distance_km, time_diff_seconds, user_id)
                        raise HTTPException(status_code=400, detail="Visit rejected: teleport detected")

                    if speed_kmh > 150.0:
                        logger.warning("Visit rejected: excessive speed %.1f km/h for user %s",
                                       speed_kmh, user_id)
                        raise HTTPException(status_code=400, detail="Visit rejected: excessive speed detected")

        except (ValueError, AttributeError, UnicodeDecodeError) as e:
            logger.error("Error during speed check for user %s: %s", user_id, e)
            # В случае ошибки проверки скорости разрешаем визит, но логируем ошибку

    conn = db_module.get_connection()

    lat, lon = float(body.lat), float(body.lon)

    geokey = h3.latlng_to_cell(lat, lon, db_module.BASE_VISIT_RESOLUTION)

    district_row = db_module.select_district_for_cell(conn, geokey)
    if not district_row:
        logger.info("Visit ignored: no district for geokey=%s", geokey)
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
                logger.info("Invalidated cache for user %s stats summary", user_id)
            except (ConnectionError, TimeoutError) as e:
                logger.warning("Error invalidating Redis cache: %s", e)

        return models.VisitResponse(
            added=0,
            circle=models.Circle(lat=lat, lon=lon),
            stats=stats,
        )

    district_id, coverage = district_row
    okrug_id = db_module.select_district_parent(conn, district_id)

    # Быстрая запись только атомарного визита
    added = db_module.record_atomic_visit(
        conn,
        user_id=user_id,
        h3_index=geokey,
    )

    # Получаем статистику (может быть с задержкой обновления)
    stats_dict = db_module.fetch_user_stats(
        conn,
        user_id=user_id,
        district_id=district_id,
        okrug_id=okrug_id,
    )
    district_stats = stats_dict.get("district")
    okrug_stats = stats_dict.get("okrug")
    stats = models.VisitStats(
        total_circles=stats_dict["total_circles"],
        district=models.RegionStats(**district_stats) if district_stats else None,
        okrug=models.RegionStats(**okrug_stats) if okrug_stats else None,
    )

    # Инвалидируем кэш статистики пользователя
    if redis_client:
        try:
            cache_key = f"user:{user_id}:stats_summary"
            await redis_client.delete(cache_key)
            logger.info("Invalidated cache for user %s stats summary", user_id)
        except (ConnectionError, TimeoutError) as e:
            logger.warning("Error invalidating Redis cache: %s", e)

    # Отправляем сообщение в RabbitMQ о визите сразу после успешной записи
    if added:  # Только если визит был добавлен (не повтор)
        try:
            current_timestamp = int(time.time())
            await queue.publish_visit_message(
                user_id=user_id,
                h3_geokey=geokey,
                lat=lat,
                lon=lon,
                timestamp=current_timestamp
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Error publishing visit message to RabbitMQ for user %s: %s", user_id, e)
            # Не прерываем обработку визита из-за ошибки RabbitMQ

    # Обновляем данные последнего визита в Redis
    if redis_client:
        try:
            last_visit_key = f"user:{user_id}:last_visit"
            current_timestamp = time.time()
            visit_data = f"{current_timestamp},{lat},{lon}"
            await redis_client.set(last_visit_key, visit_data)
            logger.info("Updated last visit data for user %s: %s", user_id, visit_data)
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error updating last visit data in Redis for user %s: %s", user_id, e)

    logger.info(
        "Visit processed: added=%s, district_id=%s, okrug_id=%s, geokey=%s, coverage=%.3f",
        added, district_id, okrug_id, geokey, coverage
    )
    return models.VisitResponse(
        added=1 if added else 0,
        circle=models.Circle(lat=lat, lon=lon),
        stats=stats,
    )


@router.get("/circles", response_model=models.CirclesResponse)
async def list_circles(bbox: str = Query(..., description="Bounding box coordinates in format 'minLon,minLat,maxLon,maxLat'"), user=Depends(get_current_user)):
    """Get user's visited circles within a bounding box.

    Returns all H3 hexagons that the authenticated user has visited
    and that intersect with the specified bounding box.

    Args:
        bbox: Bounding box coordinates in format "minLon,minLat,maxLon,maxLat"

    Returns:
        Response containing array of visited hexagon coordinates.
    """
    # DEBUG_AUTH_MODE check is now in get_current_user
    user_id, _ = user
    logger.info("Circles request: bbox=%s, user_id=%s", bbox, user_id)

    try:
        min_lon_str, min_lat_str, max_lon_str, max_lat_str = bbox.split(",")
        min_lon, min_lat = float(min_lon_str), float(min_lat_str)
        max_lon, max_lat = float(max_lon_str), float(max_lat_str)
    except Exception as e:
        logger.error("Invalid bbox format: %s, error: %s", bbox, e)
        raise HTTPException(status_code=400, detail="bad bbox") from e

    logger.info("Parsed bbox: min_lon=%s, min_lat=%s, max_lon=%s, max_lat=%s",
                min_lon, min_lat, max_lon, max_lat)

    conn = db_module.get_connection()

    # Debug: get all user hexagons first
    all_user_hexagons = db_module.select_user_hexes(conn, user_id)
    logger.info("User %s has %s total hexagons", user_id, len(all_user_hexagons))

    hexagons = db_module.select_user_hexes_in_bbox(
        conn,
        user_id=user_id,
        min_lat=min_lat,
        min_lon=min_lon,
        max_lat=max_lat,
        max_lon=max_lon,
    )

    logger.info("Circles response: %s hexagons returned in bbox", len(hexagons))
    if len(hexagons) > 0:
        logger.info("Sample hexagons: %s", hexagons[:3])
    return models.CirclesResponse(hexagons=hexagons)


@router.delete("/circle")
async def delete_circle(body: models.DeleteCircleRequest, user=Depends(get_current_user)):
    """Delete a visited circle.

    Removes a visit record for a specific H3 hexagon for the authenticated user.
    This is typically used for debugging or correcting erroneous visits.

    Args:
        body: Delete request containing the H3 geokey of the circle to remove

    Returns:
        Object with "deleted" field indicating whether the circle was successfully removed.
    """
    user_id, _ = user
    conn = db_module.get_connection()
    deleted = db_module.delete_visit_by_hex(conn, user_id=user_id, h3_index=body.geokey)
    return {"deleted": int(deleted)}
