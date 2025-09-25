import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from services.geo_service.app.main import app


class GeoServiceAPITestCase(unittest.TestCase):
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
        """Seed database with test districts and visits"""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS districts (
                id INTEGER PRIMARY KEY,
                level TEXT NOT NULL,
                name_ru TEXT NOT NULL,
                parent_id INTEGER,
                geom GEOMETRY(Geometry, 4326) NOT NULL,
                geom_geojson TEXT,
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
            """
        )

        # Create test okrugs and districts
        conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom, geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'okrug', ?, NULL, ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))', 4326), '{}', 0.0, 0.0, 1.0, 1.0, 0, 0.0)
            """,
            (10, "Test Okrug"),
        )
        conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom, geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, ST_GeomFromText('POLYGON((0 0, 0.5 0, 0.5 0.5, 0 0.5, 0 0))', 4326), '{}', 0.0, 0.0, 0.5, 0.5, 2, 1.5)
            """,
            (100, "Test District", 10),
        )

        # Add district cells
        conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "866ffffffffffff", 0.8),
        )
        conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "8663fffffffffff", 0.7),
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

    def test_debug_mode_endpoint(self):
        """Test debug mode endpoint"""
        response = self.client.get("/api/v1/debug-mode")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("debug_auth_mode", data)
        self.assertIn("no_auth_mode", data)
        self.assertIn("base_visit_resolution", data)

    def test_list_circles_success(self):
        """Test listing circles within bbox"""
        # Insert a test circle
        from app import db as db_module
        conn = db_module.get_connection()
        db_module.insert_circle_if_new(
            conn, user_id=1, geokey="test_key", lat=0.25, lon=0.25
        )

        response = self.client.get("/api/v1/circles?bbox=0.0,0.0,1.0,1.0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("hexagons", data)
        self.assertEqual(len(data["hexagons"]), 1)

    def test_list_circles_invalid_bbox(self):
        """Test circles endpoint with invalid bbox"""
        response = self.client.get("/api/v1/circles?bbox=invalid")
        self.assertEqual(response.status_code, 400)

    def test_list_districts_success(self):
        """Test listing districts within bbox"""
        response = self.client.get("/api/v1/districts?bbox=0.0,0.0,1.0,1.0&level=district")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Test District")
        self.assertEqual(data[0]["level"], "district")

    def test_list_districts_okrug_level(self):
        """Test listing okrugs within bbox"""
        response = self.client.get("/api/v1/districts?bbox=0.0,0.0,1.0,1.0&level=okrug")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Test Okrug")
        self.assertEqual(data[0]["level"], "okrug")

    def test_list_districts_invalid_level(self):
        """Test districts endpoint with invalid level"""
        response = self.client.get("/api/v1/districts?bbox=0.0,0.0,1.0,1.0&level=invalid")
        self.assertEqual(response.status_code, 422)  # Validation error

    def test_list_districts_invalid_bbox(self):
        """Test districts endpoint with invalid bbox"""
        response = self.client.get("/api/v1/districts?bbox=invalid&level=district")
        self.assertEqual(response.status_code, 400)

    def test_get_district_cells_success(self):
        """Test getting district cells"""
        response = self.client.get("/api/v1/district/100/cells")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["district_id"], 100)
        self.assertIn("cells", data)
        self.assertEqual(len(data["cells"]), 2)
        self.assertEqual(data["resolution"], 9)  # Base resolution

    def test_get_district_cells_with_resolution(self):
        """Test getting district cells with custom resolution"""
        response = self.client.get("/api/v1/district/100/cells?res_view=8")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["resolution"], 8)

    def test_get_district_cells_invalid_resolution(self):
        """Test district cells with invalid resolution"""
        response = self.client.get("/api/v1/district/100/cells?res_view=20")
        self.assertEqual(response.status_code, 400)

    def test_get_district_cells_not_found(self):
        """Test getting cells for non-existent district"""
        response = self.client.get("/api/v1/district/999/cells")
        self.assertEqual(response.status_code, 404)

    @patch('services.geo_service.app.main.cache.get_redis')
    def test_get_stats_summary_success(self, mock_get_redis):
        """Test getting stats summary"""
        # Mock Redis to return None (cache miss)
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        response = self.client.get("/api/v1/stats/summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total", data)
        self.assertIn("okrugs", data)
        self.assertIn("bottom_districts", data)
        self.assertEqual(len(data["bottom_districts"]), 1)

    @patch('services.geo_service.app.main.cache.get_redis')
    def test_get_stats_summary_cached(self, mock_get_redis):
        """Test getting cached stats summary"""
        # Mock Redis to return cached data
        cached_data = {
            "total": {"visited_cells": 1, "total_cells": 2, "percent": 50.0, "visited_weight": 0.8, "total_weight": 1.5},
            "okrugs": [],
            "bottom_districts": []
        }
        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(cached_data)
        mock_get_redis.return_value = mock_redis

        response = self.client.get("/api/v1/stats/summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"]["visited_cells"], 1)

    @patch('services.geo_service.app.main.cache.get_redis')
    def test_get_leaderboard_success(self, mock_get_redis):
        """Test getting leaderboard"""
        # Mock Redis to return None (cache miss)
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        response = self.client.get("/api/v1/leaderboard?level=district&period=week&limit=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("level", data)
        self.assertIn("period", data)
        self.assertIn("entries", data)
        self.assertIn("generated_at", data)
        self.assertEqual(data["level"], "district")
        self.assertEqual(data["period"], "week")

    def test_get_leaderboard_invalid_level(self):
        """Test leaderboard with invalid level"""
        response = self.client.get("/api/v1/leaderboard?level=invalid&period=week&limit=10")
        self.assertEqual(response.status_code, 422)

    def test_get_leaderboard_invalid_period(self):
        """Test leaderboard with invalid period"""
        response = self.client.get("/api/v1/leaderboard?level=district&period=invalid&limit=10")
        self.assertEqual(response.status_code, 422)

    def test_get_leaderboard_invalid_limit(self):
        """Test leaderboard with invalid limit"""
        response = self.client.get("/api/v1/leaderboard?level=district&period=week&limit=0")
        self.assertEqual(response.status_code, 422)

        response = self.client.get("/api/v1/leaderboard?level=district&period=week&limit=200")
        self.assertEqual(response.status_code, 422)

    @patch('services.geo_service.app.main.cache.get_redis')
    def test_get_leaderboard_cached(self, mock_get_redis):
        """Test getting cached leaderboard"""
        cached_data = {
            "level": "district",
            "period": "week",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": []
        }
        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(cached_data)
        mock_get_redis.return_value = mock_redis

        response = self.client.get("/api/v1/leaderboard?level=district&period=week&limit=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["level"], "district")

    def test_auth_debug_endpoints_disabled(self):
        """Test that debug auth endpoints are disabled in normal mode"""
        # These endpoints should only work in debug mode
        response = self.client.post("/api/auth", json={"initData": "test"})
        self.assertEqual(response.status_code, 404)  # Not found in normal mode

        response = self.client.get("/api/me")
        self.assertEqual(response.status_code, 404)

    def test_missing_auth_headers(self):
        """Test endpoints with missing authentication headers"""
        # Temporarily disable no-auth mode
        os.environ.pop("NO_AUTH_MODE", None)
        try:
            response = self.client.get("/api/v1/circles?bbox=0.0,0.0,1.0,1.0")
            self.assertEqual(response.status_code, 422)  # Missing required header
        finally:
            os.environ["NO_AUTH_MODE"] = "1"

    def test_parse_bbox_valid(self):
        """Test bbox parsing with valid input"""
        from services.geo_service.app.main import _parse_bbox
        min_lon, min_lat, max_lon, max_lat = _parse_bbox("1.0,2.0,3.0,4.0")
        self.assertEqual((min_lon, min_lat, max_lon, max_lat), (1.0, 2.0, 3.0, 4.0))

    def test_parse_bbox_invalid_format(self):
        """Test bbox parsing with invalid format"""
        from services.geo_service.app.main import _parse_bbox
        with self.assertRaises(Exception):
            _parse_bbox("invalid")

    def test_parse_bbox_invalid_order(self):
        """Test bbox parsing with invalid coordinate order"""
        from services.geo_service.app.main import _parse_bbox
        with self.assertRaises(Exception):
            _parse_bbox("3.0,4.0,1.0,2.0")  # max before min


if __name__ == "__main__":
    unittest.main()
