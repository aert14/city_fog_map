import os
import json
import logging
from typing import Optional
import pika
from pika.adapters.asyncio_connection import AsyncioConnection
from pika.channel import Channel
import asyncio

logger = logging.getLogger(__name__)

# Глобальные переменные для соединения с RabbitMQ
rabbit_connection: Optional[AsyncioConnection] = None
rabbit_channel: Optional[Channel] = None
rabbit_queue_name = "visits_queue"


async def init_rabbitmq_connection() -> None:
    """Инициализирует соединение с RabbitMQ"""
    global rabbit_connection, rabbit_channel

    rabbitmq_url = os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        logger.warning(
            "RABBITMQ_URL not set, RabbitMQ integration will be disabled"
        )
        return

    try:
        # Парсим URL для получения параметров соединения
        parameters = pika.URLParameters(rabbitmq_url)

        # Создаем асинхронное соединение
        loop = asyncio.get_event_loop()
        rabbit_connection = AsyncioConnection(
            parameters=parameters,
            on_open_callback=on_connection_open,
            on_open_error_callback=on_connection_open_error,
            on_close_callback=on_connection_closed,
            custom_ioloop=loop
        )

        logger.info("RabbitMQ connection initialization started")

    except Exception as e:
        logger.error(f"Failed to initialize RabbitMQ connection: {e}")
        rabbit_connection = None


def on_connection_open(connection: AsyncioConnection) -> None:
    """Callback при успешном открытии соединения"""
    global rabbit_channel
    logger.info("RabbitMQ connection opened")
    connection.channel(on_open_callback=on_channel_open)


def on_connection_open_error(connection: AsyncioConnection, error: Exception) -> None:
    """Callback при ошибке открытия соединения"""
    logger.error(f"RabbitMQ connection open error: {error}")
    global rabbit_connection
    rabbit_connection = None


def on_connection_closed(connection: AsyncioConnection, reason: Exception) -> None:
    """Callback при закрытии соединения"""
    logger.info(f"RabbitMQ connection closed: {reason}")
    global rabbit_connection, rabbit_channel
    rabbit_connection = None
    rabbit_channel = None


def on_channel_open(channel: Channel) -> None:
    """Callback при успешном открытии канала"""
    global rabbit_channel
    logger.info("RabbitMQ channel opened")
    rabbit_channel = channel
    # Объявляем очередь
    channel.queue_declare(
        queue=rabbit_queue_name,
        durable=True,
        callback=on_queue_declare_ok
    )


def on_queue_declare_ok(method_frame) -> None:
    """Callback при успешном объявлении очереди"""
    logger.info(f"RabbitMQ queue '{rabbit_queue_name}' declared successfully")


async def close_rabbitmq_connection() -> None:
    """Закрывает соединение с RabbitMQ"""
    global rabbit_connection, rabbit_channel

    if rabbit_channel:
        try:
            await asyncio.get_event_loop().run_in_executor(None, rabbit_channel.close)
            logger.info("RabbitMQ channel closed")
        except Exception as e:
            logger.error(f"Error closing RabbitMQ channel: {e}")
        finally:
            rabbit_channel = None

    if rabbit_connection:
        try:
            await asyncio.get_event_loop().run_in_executor(None, rabbit_connection.close)
            logger.info("RabbitMQ connection closed")
        except Exception as e:
            logger.error(f"Error closing RabbitMQ connection: {e}")
        finally:
            rabbit_connection = None


async def publish_visit_message(
    user_id: int,
    h3_geokey: str,
    lat: float,
    lon: float,
    timestamp: int
) -> bool:
    """
    Публикует сообщение о визите в очередь RabbitMQ

    Args:
        user_id: ID пользователя
        h3_geokey: H3 геоключ
        lat: Широта
        lon: Долгота
        timestamp: Временная метка Unix

    Returns:
        True если сообщение опубликовано успешно, False в противном случае
    """
    global rabbit_channel

    if not rabbit_channel:
        logger.warning("RabbitMQ channel not available, skipping message publish")
        return False

    try:
        message = {
            "user_id": user_id,
            "h3_geokey": h3_geokey,
            "lat": lat,
            "lon": lon,
            "timestamp": timestamp
        }

        message_json = json.dumps(message, ensure_ascii=False)

        # Публикуем сообщение в очередь
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: rabbit_channel.basic_publish(
                exchange='',
                routing_key=rabbit_queue_name,
                body=message_json.encode('utf-8'),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # persistent
                    content_type='application/json'
                )
            )
        )

        logger.info(f"Published visit message for user {user_id} at ({lat}, {lon})")
        return True

    except Exception as e:
        logger.error(f"Failed to publish visit message: {e}")
        return False
