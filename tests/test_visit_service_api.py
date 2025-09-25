import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import json
import hmac
import hashlib
import urllib.parse
import pika

from fastapi.testclient import TestClient
from services.visit_service.app.main import app


class VisitServiceAPITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.sqlite3")
        os.environ["DB_PATH"] = self.db_path
        os.environ["NO_AUTH_MODE"] = "1"  # Enable no-auth mode for testing

        # Create test client
        self.client = TestClient(app)

        # Initialize database
        from app import db as db_module
        conn = db_module.get_connection()
        db_module.init_db(conn)

        # Seed with test data
        self._seed_test_data(conn)

    def tearDown(self) -> None:
        os.environ.pop("DB_PATH", None)
        os.environ.pop("NO_AUTH_MODE", None)
        self.tmpdir.cleanup()

    def _seed_test_data(self, conn):
        """Seed database with test districts"""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS districts (
                id INTEGER PRIMARY KEY,
                level TEXT NOT NULL,
                name_ru TEXT NOT NULL,
                parent_id INTEGER,
                geom_geojson TEXT NOT NULL,
                bbox_min_lon REAL,
                bbox_min_lat REAL,
                bbox_max_lon REAL,
                bbox_max_lat REAL,
                total_cells INTEGER DEFAULT 0,
                total_weight REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS district_cells (
                district_id INTEGER NOT NULL,
                h3 TEXT NOT NULL,
                coverage REAL NOT NULL,
                PRIMARY KEY (district_id, h3)
            );

            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL UNIQUE,
                username TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_visits_atomic (
                user_id INTEGER NOT NULL,
                h3 TEXT NOT NULL,
                ts BIGINT NOT NULL,
                PRIMARY KEY (user_id, h3),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )

        # Create test district
        conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, NULL, '{}', -1.0, -1.0, 1.0, 1.0, 1, 1.0)
            """,
            (100, "Test District"),
        )

        # Add district cell
        conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "866ffffffffffff", 0.8),
        )

        # Create test user
        conn.execute(
            "INSERT INTO users (id, tg_id, username) VALUES (?, ?, ?)",
            (1, 999999, "testuser")
        )

        conn.commit()

    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch('services.visit_service.app.main.publish_to_rabbitmq')
    def test_visit_area_success(self, mock_publish):
        """Test successful visit recording"""
        mock_publish.return_value = None  # Mock successful publish

        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 55.7558, "lon": 37.6176}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "accepted")
        self.assertIsInstance(data["h3_geokey"], str)

        # Verify RabbitMQ publish was called
        mock_publish.assert_called_once()
        call_args = mock_publish.call_args[0][0]
        self.assertEqual(call_args["user_id"], 1)  # From no-auth mode
        self.assertEqual(call_args["lat"], 55.7558)
        self.assertEqual(call_args["lon"], 37.6176)
        self.assertEqual(call_args["district_id"], 100)
        self.assertEqual(call_args["coverage"], 0.8)

    @patch('services.visit_service.app.main.publish_to_rabbitmq')
    def test_visit_area_no_district(self, mock_publish):
        """Test visit in area with no district"""
        mock_publish.return_value = None

        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 0.0, "lon": 0.0}  # Outside test district
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "accepted")
        self.assertIsInstance(data["h3_geokey"], str)

        # Verify RabbitMQ publish was still called
        mock_publish.assert_called_once()
        call_args = mock_publish.call_args[0][0]
        self.assertEqual(call_args["district_id"], None)
        self.assertEqual(call_args["coverage"], 0.0)

    def test_visit_area_invalid_coordinates(self):
        """Test visit with invalid coordinates"""
        # Test latitude out of range
        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 100.0, "lon": 37.6176}
        )
        self.assertEqual(response.status_code, 422)  # Validation error

        # Test longitude out of range
        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 55.7558, "lon": 200.0}
        )
        self.assertEqual(response.status_code, 422)  # Validation error

    @patch('services.visit_service.app.main.publish_to_rabbitmq')
    def test_visit_area_rabbitmq_failure(self, mock_publish):
        """Test visit when RabbitMQ publish fails"""
        mock_publish.side_effect = Exception("RabbitMQ connection failed")

        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 55.7558, "lon": 37.6176}
        )

        # Should still return 200 (async processing)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "accepted")

    def test_missing_auth_headers(self):
        """Test visit endpoint with missing authentication headers"""
        # Temporarily disable no-auth mode
        os.environ.pop("NO_AUTH_MODE", None)
        try:
            response = self.client.post(
                "/api/v1/visit",
                json={"lat": 55.7558, "lon": 37.6176}
            )
            self.assertEqual(response.status_code, 422)  # Missing required header
        finally:
            os.environ["NO_AUTH_MODE"] = "1"


class RabbitMQPublishTestCase(unittest.TestCase):
    """Test cases for RabbitMQ message publishing"""

    def setUp(self):
        self.test_message = {
            "user_id": 1,
            "h3_geokey": "866ffffffffffff",
            "lat": 55.7558,
            "lon": 37.6176,
            "timestamp": 1234567890,
            "district_id": 100,
            "coverage": 0.8
        }

    @patch('pika.BlockingConnection')
    def test_publish_to_rabbitmq_success(self, mock_connection):
        """Test successful RabbitMQ message publishing"""
        mock_channel = MagicMock()
        mock_connection.return_value.channel.return_value = mock_channel

        from services.visit_service.app.main import publish_to_rabbitmq
        publish_to_rabbitmq(self.test_message)

        # Verify connection and channel creation
        mock_connection.assert_called_once()
        mock_connection.return_value.channel.assert_called_once()

        # Verify queue declaration
        mock_channel.queue_declare.assert_called_once_with(queue="visits_queue", durable=True)

        # Verify message publishing
        mock_channel.basic_publish.assert_called_once()
        call_kwargs = mock_channel.basic_publish.call_args[1]
        self.assertEqual(call_kwargs["exchange"], "")
        self.assertEqual(call_kwargs["routing_key"], "visits_queue")
        self.assertEqual(call_kwargs["properties"].delivery_mode, 2)  # Persistent

        # Parse published message
        published_body = json.loads(call_kwargs["body"])
        self.assertEqual(published_body, self.test_message)

        # Verify connection close
        mock_connection.return_value.close.assert_called_once()

    @patch('pika.BlockingConnection')
    def test_publish_to_rabbitmq_connection_failure(self, mock_connection):
        """Test RabbitMQ publishing with connection failure"""
        mock_connection.side_effect = pika.exceptions.AMQPConnectionError("Connection failed")

        from services.visit_service.app.main import publish_to_rabbitmq
        with self.assertRaises(Exception) as context:
            publish_to_rabbitmq(self.test_message)

        self.assertIn("Connection failed", str(context.exception))


class VisitServiceAuthTestCase(unittest.TestCase):
    """Test cases for authentication in visit service"""

    def setUp(self):
        # Mock Telegram bot token
        self.bot_token = "test_bot_token_12345"
        self.test_user = {
            "id": 123456,
            "username": "testuser",
            "first_name": "Test",
            "last_name": "User"
        }

    def test_get_user_from_header_valid(self):
        """Test getting user from valid Telegram initData"""
        # Create valid initData
        auth_date = int(__import__('time').time())
        data = {
            "auth_date": str(auth_date),
            "user": json.dumps(self.test_user),
            "query_id": "test_query_123"
        }

        # Create data check string and hash
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(
            "WebAppData".encode(),
            self.bot_token.encode(),
            hashlib.sha256
        ).digest()
        hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        data["hash"] = hash_value
        raw_init_data = urllib.parse.urlencode(data)

        from services.visit_service.app.main import get_user_from_header
        with patch('services.visit_service.app.main.TELEGRAM_BOT_TOKEN', self.bot_token):
            with patch('services.visit_service.app.main.db_module.get_connection') as mock_conn:
                mock_connection = MagicMock()
                mock_conn.return_value = mock_connection
                mock_connection.cursor.return_value.__enter__.return_value.fetchone.return_value = [1]

                user_id, username = get_user_from_header(raw_init_data)
                self.assertEqual(user_id, 1)
                self.assertEqual(username, "testuser")

    def test_get_user_from_header_invalid(self):
        """Test getting user with invalid initData"""
        invalid_init_data = "invalid_data"

        from services.visit_service.app.main import get_user_from_header
        with patch('services.visit_service.app.main.TELEGRAM_BOT_TOKEN', self.bot_token):
            with self.assertRaises(Exception) as context:
                get_user_from_header(invalid_init_data)
            self.assertIn("bad initData", str(context.exception))


if __name__ == "__main__":
    unittest.main()
