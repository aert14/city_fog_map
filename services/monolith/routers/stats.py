import logging
from typing import Literal, Optional, Tuple
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis

from services.common import database as db_module
from common import models
import cache

# Configure JSON logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stats"])


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


def _get_user_from_header(telegram_init: str) -> tuple[int, Optional[str]]:
    """Аутентификация через header - упрощенная версия"""
    from services.monolith.main import TELEGRAM_BOT_TOKEN

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        raise Exception("Server misconfigured: TELEGRAM_BOT_TOKEN not set")

    result = _verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        raise Exception(result.get("reason", "bad initData"))

    payload = result["payload"]
    user_raw = payload.get("user")
    if not user_raw:
        raise Exception("no user in initData")

    try:
        import json
        user_obj = json.loads(user_raw)
        tg_id = int(user_obj["id"])
        username = user_obj.get("username")
    except (json.JSONDecodeError, KeyError, ValueError):
        raise Exception("bad user json")

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
) -> tuple[int, Optional[str]]:
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
        raise Exception("Authentication via session only in debug auth mode")

    if not telegram_init:
        logger.warning("No telegram_init provided")
        raise Exception("missing initData")

    return _get_user_from_header(telegram_init)


@router.get("/stats/summary", response_model=models.StatsSummaryResponse)
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
    okrugs: list[models.OkrugSummaryEntry] = []
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
    bottom_districts: list[models.DistrictSummaryEntry] = []
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


@router.get("/leaderboard", response_model=models.LeaderboardResponse)
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

        entries: list[models.LeaderboardEntry] = []
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
