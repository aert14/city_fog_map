import os
import json
import time
import logging
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
import h3
import pika
from pythonjsonlogger import jsonlogger

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import db as db_module

# Import tracing from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
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
tracing.setup_tracing("visit-service")

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
NO_AUTH_MODE = os.getenv("NO_AUTH_MODE", "0") == "1"

# Constants
VISITS_QUEUE = "visits_queue"


class VisitRequest(BaseModel):
    lat: float
    lon: float


class Circle(BaseModel):
    lat: float
    lon: float


class RegionStats(BaseModel):
    id: int
    visited_cells: int
    visited_weight: float


class VisitStats(BaseModel):
    total_circles: int
    district: Optional[RegionStats] = None
    okrug: Optional[RegionStats] = None


class VisitResponse(BaseModel):
    added: int
    circle: Circle
    stats: VisitStats


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

# Add Prometheus metrics
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)

@app.post("/api/v1/visit", response_model=VisitResponse)
async def visit_area(request: Request, body: VisitRequest):
    """Handle visit requests - records atomic visits and returns stats"""
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

    conn = db_module.get_connection()

    # Get district info
    district_row = db_module.select_district_for_cell(conn, geokey)
    if not district_row:
        logger.info(f"Visit ignored: no district for geokey={geokey}")
        stats_dict = db_module.fetch_user_stats(conn, user_id=user_id, district_id=None, okrug_id=None)
        stats = VisitStats(
            total_circles=stats_dict["total_circles"],
            district=None,
            okrug=None,
        )
        return VisitResponse(
            added=0,
            circle=Circle(lat=lat, lon=lon),
            stats=stats,
        )

    district_id, coverage = district_row
    okrug_id = db_module.select_district_parent(conn, district_id)

    # Perform visit recording and stats increment
    added = db_module.record_visit_and_increment_stats(
        conn,
        user_id=user_id,
        h3_index=geokey,
        district_id=district_id,
        coverage=coverage,
        okrug_id=okrug_id,
    )

    # Publish message to queue for any additional processing
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

    # Get updated stats
    stats_dict = db_module.fetch_user_stats(
        conn,
        user_id=user_id,
        district_id=district_id,
        okrug_id=okrug_id,
    )
    stats = VisitStats(
        total_circles=stats_dict["total_circles"],
        district=RegionStats(**stats_dict["district"]) if stats_dict.get("district") else None,
        okrug=RegionStats(**stats_dict["okrug"]) if stats_dict.get("okrug") else None,
    )

    # Always return success with stats
    logger.info(f"Visit processed: added={added}, district_id={district_id}, okrug_id={okrug_id}, geokey={geokey}")
    return VisitResponse(
        added=1 if added else 0,
        circle=Circle(lat=lat, lon=lon),
        stats=stats,
    )


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}