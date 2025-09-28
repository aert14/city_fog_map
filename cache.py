import os
import logging
from typing import Optional
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Глобальный клиент Redis
redis_client: Optional[Redis] = None


async def init_redis_pool() -> None:
    """Инициализирует пул соединений Redis"""
    global redis_client

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning("REDIS_URL not set, Redis caching will be disabled")
        return

    try:
        redis_client = Redis.from_url(redis_url)
        # Проверяем соединение
        await redis_client.ping()
        logger.info("Redis connection established successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        redis_client = None


async def close_redis_pool() -> None:
    """Закрывает пул соединений Redis"""
    global redis_client

    if redis_client:
        try:
            await redis_client.close()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")
        finally:
            redis_client = None


async def get_redis() -> Optional[Redis]:
    """Зависимость FastAPI для получения клиента Redis"""
    return redis_client


async def check_rate_limit(user_id: int, limit: int = 20) -> bool:
    """
    Проверяет, не превышен ли лимит запросов для пользователя.

    Args:
        user_id: ID пользователя
        limit: Максимальное количество запросов в окне (60 секунд)

    Returns:
        True если лимит не превышен, False если превышен
    """
    logger.debug(f"Checking rate limit for user {user_id}, redis_client: {redis_client}")
    if not redis_client:
        # Если Redis недоступен, разрешаем запрос
        logger.warning("Redis unavailable, allowing request without rate limiting")
        return True

    try:
        key = f"rate_limit:user:{user_id}"
        current_count = await redis_client.get(key)

        if current_count is None:
            # Первый запрос в окне
            return True

        count = int(current_count)
        return count < limit

    except Exception as e:
        logger.error(f"Error checking rate limit for user {user_id}: {e}")
        # В случае ошибки разрешаем запрос
        return True


async def increment_rate_limit(user_id: int, window_seconds: int = 60) -> None:
    """
    Увеличивает счетчик запросов для пользователя.

    Args:
        user_id: ID пользователя
        window_seconds: Размер окна в секундах (для установки TTL)
    """
    if not redis_client:
        logger.warning("Redis unavailable, skipping rate limit increment")
        return

    try:
        key = f"rate_limit:user:{user_id}"
        # INCR возвращает новое значение счетчика
        new_count = await redis_client.incr(key)

        # Если это первый запрос (new_count == 1), устанавливаем TTL
        if new_count == 1:
            await redis_client.expire(key, window_seconds)

        logger.debug(f"Rate limit for user {user_id}: {new_count}")

    except Exception as e:
        logger.error(f"Error incrementing rate limit for user {user_id}: {e}")
