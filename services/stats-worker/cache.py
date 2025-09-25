import os
import logging
from typing import Optional
from redis.asyncio import Redis
import redis

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


def get_redis_sync() -> Optional[redis.Redis]:
    """Возвращает синхронный клиент Redis для использования в синхронном коде"""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None

    try:
        return redis.from_url(redis_url)
    except Exception as e:
        logger.error(f"Failed to create sync Redis client: {e}")
        return None


def init_redis_sync() -> None:
    """Синхронная инициализация Redis для использования в синхронном коде"""
    global redis_client

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning("REDIS_URL not set, Redis caching will be disabled")
        return

    try:
        import asyncio
        # Создаем новый event loop для async операций
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _init():
            global redis_client
            redis_client = Redis.from_url(redis_url)
            await redis_client.ping()
            logger.info("Redis connection established successfully")

        loop.run_until_complete(_init())
        loop.close()
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        redis_client = None
