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
