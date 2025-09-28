import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import json
import hmac
import hashlib
import urllib.parse

from fastapi.testclient import TestClient
from services.monolith.main import app


class MonolithAPITestCase(unittest.TestCase):
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
        conn.commit()

    def test_health_endpoint(self):
        """Test health check endpoint"""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ping_endpoint(self):
        """Test ping endpoint"""
        response = self.client.get("/api/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_debug_mode_endpoint(self):
        """Test debug mode endpoint"""
        response = self.client.get("/api/v1/debug-mode")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("debug_auth_mode", data)
        self.assertIn("no_auth_mode", data)
        self.assertIn("base_visit_resolution", data)

    @patch('services.monolith.main.db_module.select_district_for_cell')
    @patch('services.monolith.main.db_module.record_visit_and_increment_stats')
    @patch('services.monolith.main.db_module.fetch_user_stats')
    def test_visit_area_success(self, mock_fetch_stats, mock_record_visit, mock_select_district):
        """Test successful visit recording"""
        # Mock database calls
        mock_select_district.return_value = (100, 0.8)
        mock_record_visit.return_value = True
        mock_fetch_stats.return_value = {
            "total_circles": 1,
            "district": {"id": 100, "visited_cells": 1, "visited_weight": 0.8},
            "okrug": None
        }

        # Make request
        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 55.7558, "lon": 37.6176},
            headers={"X-User-Tg-Id": "123", "X-User-Username": "testuser"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["added"], 1)
        self.assertEqual(data["circle"]["lat"], 55.7558)
        self.assertEqual(data["circle"]["lon"], 37.6176)
        self.assertEqual(data["stats"]["total_circles"], 1)

    @patch('services.monolith.main.db_module.select_district_for_cell')
    @patch('services.monolith.main.db_module.fetch_user_stats')
    def test_visit_area_no_district(self, mock_fetch_stats, mock_select_district):
        """Test visit in area with no district"""
        # Mock database calls - no district found
        mock_select_district.return_value = None
        mock_fetch_stats.return_value = {
            "total_circles": 0,
            "district": None,
            "okrug": None
        }

        # Make request
        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 0.0, "lon": 0.0},
            headers={"X-User-Tg-Id": "123", "X-User-Username": "testuser"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["added"], 0)
        self.assertEqual(data["circle"]["lat"], 0.0)
        self.assertEqual(data["circle"]["lon"], 0.0)
        self.assertEqual(data["stats"]["total_circles"], 0)

    def test_visit_area_invalid_coordinates(self):
        """Test visit with invalid coordinates"""
        # Test latitude out of range
        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 100.0, "lon": 37.6176},
            headers={"X-User-Tg-Id": "123", "X-User-Username": "testuser"}
        )
        self.assertEqual(response.status_code, 422)  # Validation error

        # Test longitude out of range
        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 55.7558, "lon": 200.0},
            headers={"X-User-Tg-Id": "123", "X-User-Username": "testuser"}
        )
        self.assertEqual(response.status_code, 422)  # Validation error

    @patch('services.monolith.main.db_module.clear_all')
    def test_dev_clear_db_debug_mode_disabled(self, mock_clear_all):
        """Test clear db endpoint when debug mode is disabled"""
        # Debug mode is disabled by default
        response = self.client.post("/api/v1/dev/clear-db")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "forbidden")

    @patch('services.monolith.main.db_module.clear_all')
    def test_dev_clear_db_debug_mode_enabled(self, mock_clear_all):
        """Test clear db endpoint when debug mode is enabled"""
        # Enable debug mode
        os.environ["DEBUG_AUTH_MODE"] = "1"
        try:
            mock_clear_all.return_value = (5, 2)

            response = self.client.post("/api/v1/dev/clear-db")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["cleared_circles"], 5)
            self.assertEqual(data["cleared_users"], 2)
        finally:
            os.environ.pop("DEBUG_AUTH_MODE", None)

    def test_visit_area_missing_headers(self):
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

    def test_root_redirect_debug_mode(self):
        """Test root redirect in debug mode"""
        os.environ["DEBUG_AUTH_MODE"] = "1"
        try:
            response = self.client.get("/", allow_redirects=False)
            self.assertEqual(response.status_code, 307)  # Redirect
            self.assertIn("/webapp/debug-auth.html", response.headers["location"])
        finally:
            os.environ.pop("DEBUG_AUTH_MODE", None)

    def test_root_redirect_normal_mode(self):
        """Test root redirect in normal mode"""
        response = self.client.get("/", allow_redirects=False)
        self.assertEqual(response.status_code, 307)  # Redirect
        self.assertIn("/webapp/", response.headers["location"])

    @patch('services.monolith.main.db_module.record_visit_and_increment_stats')
    @patch('services.monolith.main.db_module.select_district_for_cell')
    def test_visit_area_duplicate_visit(self, mock_select_district, mock_record_visit):
        """Test duplicate visit handling"""
        # Mock district found
        mock_select_district.return_value = (100, 0.8)
        # Mock record_visit returns False (duplicate)
        mock_record_visit.return_value = False

        response = self.client.post(
            "/api/v1/visit",
            json={"lat": 55.7558, "lon": 37.6176},
            headers={"X-User-Tg-Id": "123", "X-User-Username": "testuser"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["added"], 0)  # Should be 0 for duplicate

    @patch('services.monolith.main.db_module.select_district_for_cell')
    @patch('services.monolith.main.db_module.fetch_user_stats')
    def test_visit_area_rate_limit_graceful_degradation(self, mock_fetch_stats, mock_select_district):
        """Test rate limiter graceful degradation when Redis is unavailable"""
        # Mock database calls
        mock_select_district.return_value = (100, 0.8)
        mock_fetch_stats.return_value = {
            "total_circles": 1,
            "district": {"id": 100, "visited_cells": 1, "visited_weight": 0.8},
            "okrug": None
        }

        # Test that multiple requests succeed when Redis is not available
        # (graceful degradation - rate limiting is disabled)
        for i in range(25):  # More than the 20 request limit
            response = self.client.post(
                "/api/v1/visit",
                json={"lat": 55.7558, "lon": 37.6176},
                headers={"X-User-Tg-Id": "123", "X-User-Username": "testuser"}
            )
            self.assertEqual(response.status_code, 200,
                           f"Request {i+1} should succeed with graceful degradation")


class TelegramAuthTestCase(unittest.TestCase):
    """Test cases for Telegram authentication verification"""

    def setUp(self):
        # Mock Telegram bot token
        self.bot_token = "test_bot_token_12345"
        self.test_user = {
            "id": 123456,
            "username": "testuser",
            "first_name": "Test",
            "last_name": "User"
        }

    def test_verify_init_data_valid(self):
        """Test verification of valid initData"""
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

        from services.monolith.main import verify_init_data
        result = verify_init_data(raw_init_data, self.bot_token)

        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["user"], json.dumps(self.test_user))

    def test_verify_init_data_missing_hash(self):
        """Test verification with missing hash"""
        data = {
            "auth_date": str(int(__import__('time').time())),
            "user": json.dumps(self.test_user)
        }
        raw_init_data = urllib.parse.urlencode(data)

        from services.monolith.main import verify_init_data
        result = verify_init_data(raw_init_data, self.bot_token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing hash")

    def test_verify_init_data_invalid_hash(self):
        """Test verification with invalid hash"""
        data = {
            "auth_date": str(int(__import__('time').time())),
            "user": json.dumps(self.test_user),
            "hash": "invalid_hash_12345"
        }
        raw_init_data = urllib.parse.urlencode(data)

        from services.monolith.main import verify_init_data
        result = verify_init_data(raw_init_data, self.bot_token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "hash mismatch")

    def test_verify_init_data_expired(self):
        """Test verification with expired auth_date"""
        # Create initData with old timestamp (more than 24 hours ago)
        old_auth_date = int(__import__('time').time()) - (25 * 60 * 60)  # 25 hours ago
        data = {
            "auth_date": str(old_auth_date),
            "user": json.dumps(self.test_user)
        }

        # Create valid hash for the old data
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(
            "WebAppData".encode(),
            self.bot_token.encode(),
            hashlib.sha256
        ).digest()
        hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        data["hash"] = hash_value
        raw_init_data = urllib.parse.urlencode(data)

        from services.monolith.main import verify_init_data
        result = verify_init_data(raw_init_data, self.bot_token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stale auth_date")


if __name__ == "__main__":
    unittest.main()
