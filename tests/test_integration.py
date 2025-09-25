import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import json
import time

from fastapi.testclient import TestClient


class IntegrationTestCase(unittest.TestCase):
    """Integration tests covering full request flows across services"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.sqlite3")
        os.environ["DB_PATH"] = self.db_path
        os.environ["NO_AUTH_MODE"] = "1"  # Enable no-auth mode for testing

        # Initialize database once for all services
        from app import db as db_module
        conn = db_module.get_connection()
        db_module.init_db(conn)
        self._seed_integration_data(conn)

    def tearDown(self) -> None:
        os.environ.pop("DB_PATH", None)
        os.environ.pop("NO_AUTH_MODE", None)
        self.tmpdir.cleanup()

    def _seed_integration_data(self, conn):
        """Seed database with comprehensive test data for integration tests"""
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

            CREATE TABLE IF NOT EXISTS circles (
                user_id INTEGER NOT NULL,
                geokey TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, geokey),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_visits_atomic (
                user_id INTEGER NOT NULL,
                h3 TEXT NOT NULL,
                ts BIGINT NOT NULL,
                PRIMARY KEY (user_id, h3),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_district_stats (
                user_id INTEGER NOT NULL,
                district_id INTEGER NOT NULL,
                visited_cells INTEGER NOT NULL DEFAULT 0,
                visited_weight REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, district_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (district_id) REFERENCES districts(id)
            );

            CREATE TABLE IF NOT EXISTS user_okrug_stats (
                user_id INTEGER NOT NULL,
                okrug_id INTEGER NOT NULL,
                visited_cells INTEGER NOT NULL DEFAULT 0,
                visited_weight REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, okrug_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (okrug_id) REFERENCES districts(id)
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
            ) VALUES (?, 'okrug', ?, NULL, ST_GeomFromText('POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))', 4326), '{}', 0.0, 0.0, 10.0, 10.0, 0, 0.0)
            """,
            (10, "Central Integration Okrug"),
        )
        conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom, geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, ST_GeomFromText('POLYGON((0 0, 5 0, 5 5, 0 5, 0 0))', 4326), '{}', 0.0, 0.0, 5.0, 5.0, 4, 3.0)
            """,
            (100, "Tverskoy Integration District", 10),
        )
        conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom, geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, ST_GeomFromText('POLYGON((5 5, 10 5, 10 10, 5 10, 5 5))', 4326), '{}', 5.0, 5.0, 10.0, 10.0, 2, 1.5)
            """,
            (101, "Arbat Integration District", 10),
        )

        # Add district cells
        district_cells = [
            (100, "866ffffffffffff", 0.8),
            (100, "8663fffffffffff", 0.7),
            (100, "8664fffffffffff", 0.6),
            (100, "8665fffffffffff", 0.5),
            (101, "8666fffffffffff", 0.9),
            (101, "8667fffffffffff", 0.4),
        ]
        for district_id, h3, coverage in district_cells:
            conn.execute(
                "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
                (district_id, h3, coverage),
            )

        # Create test users
        conn.execute(
            "INSERT INTO users (id, tg_id, username) VALUES (?, ?, ?)",
            (1, 999999, "testuser1")
        )
        conn.execute(
            "INSERT INTO users (id, tg_id, username) VALUES (?, ?, ?)",
            (2, 999998, "testuser2")
        )

        conn.commit()

    @patch('services.visit_service.app.main.publish_to_rabbitmq')
    def test_full_visit_workflow(self, mock_publish):
        """Test complete visit workflow from recording to stats retrieval"""
        mock_publish.return_value = None

        # Step 1: Record a visit using visit-service
        from services.visit_service.app.main import app as visit_app
        visit_client = TestClient(visit_app)

        visit_response = visit_client.post(
            "/api/v1/visit",
            json={"lat": 2.5, "lon": 2.5}  # Inside Tverskoy district
        )
        self.assertEqual(visit_response.status_code, 200)
        visit_data = visit_response.json()
        self.assertEqual(visit_data["status"], "accepted")
        h3_key = visit_data["h3_geokey"]

        # Step 2: Simulate stats worker processing
        from app import db as db_module
        conn = db_module.get_connection()

        # Get district info
        district_info = db_module.select_district_for_cell(conn, h3_key)
        self.assertIsNotNone(district_info)
        district_id, coverage = district_info
        okrug_id = db_module.select_district_parent(conn, district_id)

        # Update stats (simulating what stats worker does)
        db_module.record_visit_and_increment_stats(
            conn,
            user_id=1,
            h3_index=h3_key,
            district_id=district_id,
            coverage=coverage,
            okrug_id=okrug_id,
        )

        # Step 3: Check stats via geo-service
        from services.geo_service.app.main import app as geo_app
        geo_client = TestClient(geo_app)

        stats_response = geo_client.get("/api/v1/stats/summary")
        self.assertEqual(stats_response.status_code, 200)
        stats_data = stats_response.json()

        # Verify stats were updated
        self.assertEqual(stats_data["total"]["visited_cells"], 1)
        self.assertGreater(stats_data["total"]["visited_weight"], 0)

        # Check district stats
        district_stats = None
        for district in stats_data["bottom_districts"]:
            if district["name"] == "Tverskoy Integration District":
                district_stats = district
                break
        self.assertIsNotNone(district_stats)
        self.assertEqual(district_stats["progress"]["visited_cells"], 1)

    def test_user_management_workflow(self):
        """Test user management workflow across services"""
        # Step 1: Get user info via user-service
        from services.user_service.app.main import app as user_app
        user_client = TestClient(user_app)

        user_response = user_client.get("/api/me")
        self.assertEqual(user_response.status_code, 200)
        user_data = user_response.json()
        self.assertEqual(user_data["username"], "testuser1")

        # Step 2: Record some circles via monolith service
        from services.monolith.main import app as monolith_app
        monolith_client = TestClient(monolith_app)

        # Record a circle (simulating legacy circle storage)
        from app import db as db_module
        conn = db_module.get_connection()
        db_module.insert_circle_if_new(
            conn, user_id=1, geokey="legacy_circle_1", lat=1.0, lon=1.0
        )

        # Step 3: Check circles via geo-service
        from services.geo_service.app.main import app as geo_app
        geo_client = TestClient(geo_app)

        circles_response = geo_client.get("/api/v1/circles?bbox=0.0,0.0,2.0,2.0")
        self.assertEqual(circles_response.status_code, 200)
        circles_data = circles_response.json()
        self.assertEqual(len(circles_data["hexagons"]), 1)

    def test_district_discovery_workflow(self):
        """Test district discovery and exploration workflow"""
        from services.geo_service.app.main import app as geo_app
        geo_client = TestClient(geo_app)

        # Step 1: Get districts in bbox
        districts_response = geo_client.get("/api/v1/districts?bbox=0.0,0.0,6.0,6.0&level=district")
        self.assertEqual(districts_response.status_code, 200)
        districts_data = districts_response.json()
        self.assertEqual(len(districts_data), 1)  # Only Tverskoy in this bbox
        self.assertEqual(districts_data[0]["name"], "Tverskoy Integration District")

        # Step 2: Get district cells
        district_id = districts_data[0]["id"]
        cells_response = geo_client.get(f"/api/v1/district/{district_id}/cells")
        self.assertEqual(cells_response.status_code, 200)
        cells_data = cells_response.json()
        self.assertEqual(cells_data["district_id"], district_id)
        self.assertEqual(len(cells_data["cells"]), 4)  # All cells at base resolution

        # Step 3: Get district cells at different resolution
        cells_response_res8 = geo_client.get(f"/api/v1/district/{district_id}/cells?res_view=8")
        self.assertEqual(cells_response_res8.status_code, 200)
        cells_data_res8 = cells_response_res8.json()
        self.assertEqual(cells_data_res8["resolution"], 8)
        # Should have aggregated cells at resolution 8

    def test_leaderboard_workflow(self):
        """Test leaderboard functionality"""
        # First, create some visit data for multiple users
        from app import db as db_module
        conn = db_module.get_connection()

        # User 1 visits
        db_module.record_visit_and_increment_stats(
            conn,
            user_id=1,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        # User 2 visits
        db_module.record_visit_and_increment_stats(
            conn,
            user_id=2,
            h3_index="8666fffffffffff",
            district_id=101,
            coverage=0.9,
            okrug_id=10,
        )

        # Get leaderboard
        from services.geo_service.app.main import app as geo_app
        geo_client = TestClient(geo_app)

        leaderboard_response = geo_client.get("/api/v1/leaderboard?level=district&period=season&limit=10")
        self.assertEqual(leaderboard_response.status_code, 200)
        leaderboard_data = leaderboard_response.json()
        self.assertIn("entries", leaderboard_data)
        self.assertGreaterEqual(len(leaderboard_data["entries"]), 1)

    def test_cross_service_data_consistency(self):
        """Test data consistency across services"""
        from app import db as db_module
        conn = db_module.get_connection()

        # Record visit via database directly
        db_module.record_visit_and_increment_stats(
            conn,
            user_id=1,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        # Check via geo-service stats
        from services.geo_service.app.main import app as geo_app
        geo_client = TestClient(geo_app)

        stats_response = geo_client.get("/api/v1/stats/summary")
        self.assertEqual(stats_response.status_code, 200)
        stats_data = stats_response.json()

        # Check total progress
        self.assertEqual(stats_data["total"]["visited_cells"], 1)

        # Check district progress
        districts_response = geo_client.get("/api/v1/districts?bbox=0.0,0.0,5.0,5.0&level=district")
        self.assertEqual(districts_response.status_code, 200)
        districts_data = districts_response.json()

        tverskoy_district = districts_data[0]
        self.assertEqual(tverskoy_district["progress"]["visited_cells"], 1)

    def test_error_handling_integration(self):
        """Test error handling across services"""
        from services.geo_service.app.main import app as geo_app
        geo_client = TestClient(geo_app)

        # Test invalid bbox
        response = geo_client.get("/api/v1/districts?bbox=invalid&level=district")
        self.assertEqual(response.status_code, 400)

        # Test non-existent district
        response = geo_client.get("/api/v1/district/99999/cells")
        self.assertEqual(response.status_code, 404)

        # Test invalid level
        response = geo_client.get("/api/v1/districts?bbox=0.0,0.0,1.0,1.0&level=invalid")
        self.assertEqual(response.status_code, 422)

    def test_concurrent_user_isolation(self):
        """Test that multiple users' data is properly isolated"""
        from app import db as db_module
        conn = db_module.get_connection()

        # User 1 activities
        db_module.record_visit_and_increment_stats(
            conn,
            user_id=1,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )
        db_module.insert_circle_if_new(
            conn, user_id=1, geokey="user1_circle", lat=1.0, lon=1.0
        )

        # User 2 activities
        db_module.record_visit_and_increment_stats(
            conn,
            user_id=2,
            h3_index="8666fffffffffff",
            district_id=101,
            coverage=0.9,
            okrug_id=10,
        )
        db_module.insert_circle_if_new(
            conn, user_id=2, geokey="user2_circle", lat=6.0, lon=6.0
        )

        # Check user 1 data
        user1_hexes = db_module.select_user_hexes(conn, 1)
        self.assertEqual(len(user1_hexes), 1)
        self.assertEqual(user1_hexes[0], "866ffffffffffff")

        user1_circles = db_module.select_circles_in_bbox(
            conn, user_id=1, min_lat=0.0, min_lon=0.0, max_lat=2.0, max_lon=2.0
        )
        self.assertEqual(len(user1_circles), 1)

        # Check user 2 data
        user2_hexes = db_module.select_user_hexes(conn, 2)
        self.assertEqual(len(user2_hexes), 1)
        self.assertEqual(user2_hexes[0], "8666fffffffffff")

        user2_circles = db_module.select_circles_in_bbox(
            conn, user_id=2, min_lat=5.0, min_lon=5.0, max_lat=7.0, max_lon=7.0
        )
        self.assertEqual(len(user2_circles), 1)

        # Verify no cross-contamination
        user1_stats = db_module.fetch_user_stats(conn, user_id=1, district_id=101, okrug_id=10)
        user2_stats = db_module.fetch_user_stats(conn, user_id=2, district_id=100, okrug_id=10)

        # User 1 should have no stats for district 101
        if user1_stats.get("district") and user1_stats["district"]["id"] == 101:
            self.assertEqual(user1_stats["district"]["visited_cells"], 0)

        # User 2 should have no stats for district 100
        if user2_stats.get("district") and user2_stats["district"]["id"] == 100:
            self.assertEqual(user2_stats["district"]["visited_cells"], 0)


if __name__ == "__main__":
    unittest.main()
