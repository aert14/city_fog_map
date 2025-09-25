import os
import json
import time
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import h3
import pika

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import db as db_module

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
NO_AUTH_MODE = os.getenv("NO_AUTH_MODE", "0") == "1"

# Constants
VISITS_QUEUE = "visits_queue"


class VisitRequest(BaseModel):
    lat: float
    lon: float


class VisitAcceptedResponse(BaseModel):
    status: str = "accepted"
    h3_geokey: str


def get_rabbitmq_connection():
    """Get RabbitMQ connection"""
    try:
        parameters = pika.URLParameters(RABBITMQ_URL)
        connection = pika.BlockingConnection(parameters)
        return connection
    except Exception as e:
        logger.error(f"Failed to connect to RabbitMQ: {e}")
        raise


def publish_visit_message(channel, message: dict):
    """Publish visit message to RabbitMQ queue"""
    try:
        channel.queue_declare(queue=VISITS_QUEUE, durable=True)
        channel.basic_publish(
            exchange='',
            routing_key=VISITS_QUEUE,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # make message persistent
            )
        )
        logger.info(f"Published visit message to queue: {message}")
    except Exception as e:
        logger.error(f"Failed to publish message: {e}")
        raise


def get_user_from_request(request: Request) -> int:
    """Extract user_id from request headers"""
    if NO_AUTH_MODE:
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="local")
        logger.warning("NO_AUTH_MODE enabled: using local user")
        return user_id

    # For simplicity, we'll assume user_id is passed in header
    # In real implementation, this should validate Telegram auth
    user_id_str = request.headers.get("X-User-ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")

    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")


app = FastAPI(title="Visit Service API", version="0.1.0")


@app.post("/api/v1/visit", response_model=VisitAcceptedResponse)
async def visit_area(request: Request, body: VisitRequest):
    """Handle visit requests - simplified version that only records atomic visits"""
    try:
        # For simplicity, get user_id from header
        # In production, this should be proper auth
        user_id = get_user_from_request(request)
    except HTTPException:
        # Fallback: create a test user for development
        conn = db_module.get_connection()
        user_id = db_module.ensure_user(conn, tg_id=999_999_999, username="test_user")
        logger.warning("Using test user due to auth issues")

    logger.info(f"Visit request: lat={body.lat}, lon={body.lon}, user_id={user_id}")

    # Calculate H3 geokey
    lat, lon = float(body.lat), float(body.lon)
    geokey = h3.latlng_to_cell(lat, lon, db_module.BASE_VISIT_RESOLUTION)

    # Perform atomic INSERT OR IGNORE into user_visits_atomic
    conn = db_module.get_connection()
    added = db_module.record_visit_atomic_only(conn, user_id=user_id, h3_index=geokey)

    # If this is a new visit (INSERT succeeded), publish message to queue
    if added:
        message = {
            "user_id": user_id,
            "h3_geokey": geokey,
            "lat": lat,
            "lon": lon,
            "timestamp": int(time.time())
        }

        try:
            connection = get_rabbitmq_connection()
            channel = connection.channel()
            publish_visit_message(channel, message)
            connection.close()
        except Exception as e:
            logger.error(f"Failed to publish visit message: {e}")
            # Note: We don't fail the request if queue publishing fails
            # The visit is still recorded atomically

    # Always return success with the geokey
    # This gives immediate feedback to frontend
    logger.info(f"Visit processed: added={added}, geokey={geokey}")
    return VisitAcceptedResponse(status="accepted", h3_geokey=geokey)


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}