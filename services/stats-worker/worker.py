#!/usr/bin/env python3
"""
Stats Worker - Consumer for visit messages from RabbitMQ queue.
Processes visits asynchronously and updates statistics.
"""

import os
import json
import logging
import time
import signal
import sys
from typing import Dict, Any

import pika
from prometheus_client import Counter, start_http_server
from pythonjsonlogger import jsonlogger

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # add services to path
from common import db as db_module
import cache
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
tracing.setup_tracing("stats-worker")

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Constants
VISITS_QUEUE = "visits_queue"

# Prometheus metrics
jobs_processed_total = Counter('jobs_processed_total', 'Total number of jobs processed')
jobs_failed_total = Counter('jobs_failed_total', 'Total number of failed jobs')

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True


def get_rabbitmq_connection():
    """Get RabbitMQ connection with retry logic"""
    max_retries = 30
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            parameters = pika.URLParameters(RABBITMQ_URL)
            connection = pika.BlockingConnection(parameters)
            logger.info("Connected to RabbitMQ")
            return connection
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Failed to connect to RabbitMQ (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to RabbitMQ after {max_retries} attempts: {e}")
                raise


def process_visit_message(message: Dict[str, Any]) -> bool:
    """
    Process a single visit message.
    Returns True if processing was successful.
    """
    try:
        user_id = message["user_id"]
        h3_geokey = message["h3_geokey"]
        lat = message["lat"]
        lon = message["lon"]
        timestamp = message["timestamp"]

        logger.info(f"Processing visit: user_id={user_id}, h3_geokey={h3_geokey}")

        # Get database connection
        conn = db_module.get_connection()

        # Find district and okrug for this H3 cell
        district_info = db_module.select_district_for_cell(conn, h3_geokey)
        if not district_info:
            logger.warning(f"No district found for h3_geokey={h3_geokey}, skipping stats update")
            return True  # This is not an error, just no district coverage

        district_id, coverage = district_info
        okrug_id = db_module.select_district_parent(conn, district_id)

        logger.info(f"Found district_id={district_id}, okrug_id={okrug_id}, coverage={coverage}")

        # Update statistics (this will do INSERT ... ON CONFLICT DO UPDATE)
        # Note: The atomic visit is already recorded by visit-service
        # Here we only update the derived statistics
        success = db_module.update_visit_statistics(
            conn,
            user_id=user_id,
            h3_index=h3_geokey,
            district_id=district_id,
            coverage=coverage,
            okrug_id=okrug_id,
        )

        if success:
            logger.info(f"Successfully updated statistics for user {user_id}")
            jobs_processed_total.inc()  # Increment processed jobs counter

            # Invalidate user's stats cache
            try:
                redis_client = cache.get_redis_sync()
                if redis_client:
                    cache_key = f"user:{user_id}:stats_summary"
                    redis_client.delete(cache_key)
                    logger.info(f"Invalidated cache for user {user_id} stats summary")
            except Exception as e:
                logger.warning(f"Failed to invalidate cache: {e}")
        else:
            logger.error(f"Failed to update statistics for user {user_id}")
            jobs_failed_total.inc()  # Increment failed jobs counter
            return False

        return True

    except Exception as e:
        logger.error(f"Error processing visit message: {e}", exc_info=True)
        jobs_failed_total.inc()  # Increment failed jobs counter
        return False


def callback(ch, method, properties, body):
    """Callback function for processing messages from the queue"""
    try:
        message = json.loads(body.decode('utf-8'))
        logger.info(f"Received message: {message}")

        success = process_visit_message(message)

        if success:
            # Acknowledge the message
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info("Message processed and acknowledged")
        else:
            # Reject the message and requeue it
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            logger.warning("Message processing failed, requeued")

    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode message: {e}")
        # Acknowledge malformed messages to prevent infinite loops
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        logger.error(f"Unexpected error in callback: {e}", exc_info=True)
        # Reject and requeue on unexpected errors
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def main():
    """Main worker loop"""
    logger.info("Starting Stats Worker...")

    # Start Prometheus metrics server
    start_http_server(8001)
    logger.info("Prometheus metrics server started on port 8001")

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Initialize Redis pool (for cache invalidation)
        cache.init_redis_sync()

        # Connect to RabbitMQ
        connection = get_rabbitmq_connection()
        channel = connection.channel()

        # Declare the queue
        channel.queue_declare(queue=VISITS_QUEUE, durable=True)

        # Set up QoS - prefetch only 1 message at a time
        channel.basic_qos(prefetch_count=1)

        # Set up the consumer
        channel.basic_consume(
            queue=VISITS_QUEUE,
            on_message_callback=callback
        )

        logger.info("Stats Worker started. Waiting for messages...")

        # Start consuming
        while not shutdown_requested:
            try:
                # Wait for messages with timeout to check shutdown flag
                connection.process_data_events(time_limit=1)
            except Exception as e:
                logger.error(f"Error in message processing loop: {e}")
                break

        logger.info("Shutdown requested, closing connections...")

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Clean up
        try:
            cache.close_redis_pool()
        except:
            pass

        logger.info("Stats Worker stopped")


if __name__ == "__main__":
    main()
