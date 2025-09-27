import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import json
import hmac
import hashlib
import urllib.parse
from datetime import datetime

from fastapi.testclient import TestClient
from services.user_service.app.main import app


class UserServiceAPITestCase(unittest.TestCase):
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
        """Seed database with test user"""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL UNIQUE,
                username TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                h3_resolution INTEGER NOT NULL DEFAULT 11,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )

        # Create test user
        conn.execute(
            "INSERT INTO users (id, tg_id, username, created_at) VALUES (?, ?, ?, ?)",
            (1, 999999, "testuser", datetime.utcnow().isoformat())
        )

        # Create user settings
        conn.execute(
            "INSERT INTO user_settings (user_id, h3_resolution) VALUES (?, ?)",
            (1, 12)
        )

        conn.commit()

    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_debug_mode_endpoint(self):
        """Test debug mode endpoint"""
        response = self.client.get("/api/v1/debug-mode")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("debug_auth_mode", data)
        self.assertIn("no_auth_mode", data)

    def test_get_current_user_info_success(self):
        """Test getting current user information"""
        response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], 1)
        self.assertEqual(data["tg_id"], 999999)
        self.assertEqual(data["username"], "testuser")
        self.assertIn("created_at", data)

    def test_get_current_user_info_not_found(self):
        """Test getting user info for non-existent user"""
        # Temporarily modify the user ID to non-existent
        with patch('services.user_service.app.main.get_current_user') as mock_get_user:
            mock_get_user.return_value = (999, None)  # Non-existent user ID

            response = self.client.get("/api/me")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "user not found")

    def test_debug_auth_endpoint_disabled(self):
        """Test that debug auth endpoint is disabled in normal mode"""
        response = self.client.post("/api/auth", json={"initData": "test"})
        self.assertEqual(response.status_code, 404)  # Not found in normal mode

    def test_debug_me_endpoint_disabled(self):
        """Test that debug me endpoint is disabled in normal mode"""
        response = self.client.get("/api/me/debug")
        self.assertEqual(response.status_code, 404)  # Not found in normal mode

    def test_missing_auth_headers(self):
        """Test endpoints with missing authentication headers"""
        # Temporarily disable no-auth mode
        os.environ.pop("NO_AUTH_MODE", None)
        try:
            response = self.client.get("/api/me")
            self.assertEqual(response.status_code, 422)  # Missing required header
        finally:
            os.environ["NO_AUTH_MODE"] = "1"


class UserServiceAuthTestCase(unittest.TestCase):
    """Test cases for Telegram authentication in user service"""

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

        from services.user_service.app.main import _get_user_from_header
        with patch('services.user_service.app.main.TELEGRAM_BOT_TOKEN', self.bot_token):
            with patch('services.user_service.app.main.db_module.get_connection') as mock_conn:
                mock_connection = MagicMock()
                mock_conn.return_value = mock_connection
                mock_connection.cursor.return_value.__enter__.return_value.fetchone.return_value = [1]

                user_id, username = _get_user_from_header(raw_init_data)
                self.assertEqual(user_id, 1)
                self.assertEqual(username, "testuser")

    def test_get_user_from_header_invalid_hash(self):
        """Test getting user with invalid hash"""
        data = {
            "auth_date": str(int(__import__('time').time())),
            "user": json.dumps(self.test_user),
            "hash": "invalid_hash_12345"
        }
        raw_init_data = urllib.parse.urlencode(data)

        from services.user_service.app.main import _get_user_from_header
        with patch('services.user_service.app.main.TELEGRAM_BOT_TOKEN', self.bot_token):
            with self.assertRaises(Exception) as context:
                _get_user_from_header(raw_init_data)
            self.assertIn("bad initData", str(context.exception))

    def test_get_user_from_header_no_user(self):
        """Test getting user when user data is missing"""
        data = {
            "auth_date": str(int(__import__('time').time())),
            "hash": "some_hash"
        }
        raw_init_data = urllib.parse.urlencode(data)

        from services.user_service.app.main import _get_user_from_header
        with patch('services.user_service.app.main.TELEGRAM_BOT_TOKEN', self.bot_token):
            with self.assertRaises(Exception) as context:
                _get_user_from_header(raw_init_data)
            self.assertIn("no user in initData", str(context.exception))

    def test_get_user_from_header_invalid_json(self):
        """Test getting user with invalid JSON in user field"""
        data = {
            "auth_date": str(int(__import__('time').time())),
            "user": "invalid json",
            "hash": "some_hash"
        }
        raw_init_data = urllib.parse.urlencode(data)

        from services.user_service.app.main import _get_user_from_header
        with patch('services.user_service.app.main.TELEGRAM_BOT_TOKEN', self.bot_token):
            with self.assertRaises(Exception) as context:
                _get_user_from_header(raw_init_data)
            self.assertIn("bad user json", str(context.exception))


if __name__ == "__main__":
    unittest.main()
