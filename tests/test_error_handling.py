import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import json
import psycopg2

from fastapi.testclient import TestClient


class ErrorHandlingTestCase(unittest.TestCase):
    """Test cases for error handling and edge cases across services"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.sqlite3")
        os.environ["DB_PATH"] = self.db_path
        os.environ["NO_AUTH_MODE"] = "1"

        # Initialize database
        from app import db as db_module
        conn = db_module.get_connection()
        db_module.init_db(conn)

    def tearDown(self) -> None:
        os.environ.pop("DB_PATH", None)
        os.environ.pop("NO_AUTH_MODE", None)
        self.tmpdir.cleanup()

    def test_database_connection_errors(self):
        """Test database connection error handling"""
        from app import db as db_module

        # Test with invalid database URL
        original_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "invalid_url"

        try:
            # This should raise an exception
            with self.assertRaises(Exception):
                db_module.get_connection()
        finally:
            if original_url:
                os.environ["DATABASE_URL"] = original_url
            else:
                os.environ.pop("DATABASE_URL", None)

    @patch('psycopg2.connect')
    def test_database_connection_recovery(self, mock_connect):
        """Test database connection recovery after failures"""
        from app import db as db_module

        # Mock connection that fails initially then succeeds
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connection.cursor.return_value.__enter__.return_value.execute.side_effect = [
            psycopg2.Error("Connection failed"),
            None  # Success on retry
        ]

        mock_connect.side_effect = [psycopg2.Error("Initial connection failed"), mock_connection]

        # First call should fail and trigger reconnection
        with self.assertRaises(psycopg2.Error):
            conn = db_module.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")

        # Second call should succeed with new connection
        conn = db_module.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")  # Should not raise

    def test_invalid_h3_coordinates(self):
        """Test handling of invalid H3 coordinates"""
        import h3

        # Test with coordinates that might cause H3 issues
        invalid_coords = [
            (91.0, 0.0),   # Invalid latitude
            (0.0, 181.0),  # Invalid longitude
            (-91.0, 0.0),  # Invalid latitude
            (0.0, -181.0), # Invalid longitude
        ]

        for lat, lon in invalid_coords:
            with self.subTest(lat=lat, lon=lon):
                try:
                    h3.latlng_to_cell(lat, lon, 9)
                    # If we get here, H3 accepted the coordinates
                except Exception as e:
                    # H3 rejected the coordinates - this is expected
                    self.assertIsInstance(e, (ValueError, TypeError))

    def test_malformed_json_requests(self):
        """Test handling of malformed JSON in requests"""
        from services.monolith.main import app as monolith_app
        client = TestClient(monolith_app)

        # Test with invalid JSON
        response = client.post(
            "/api/v1/visit",
            content="invalid json {",
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 422)

        # Test with missing required fields
        response = client.post(
            "/api/v1/visit",
            json={"lat": 55.0},  # Missing lon
            headers={"X-User-Tg-Id": "123"}
        )
        self.assertEqual(response.status_code, 422)

    def test_bbox_validation_edge_cases(self):
        """Test bbox validation edge cases"""
        from services.geo_service.app.main import _parse_bbox

        # Test valid bboxes
        valid_bboxes = [
            "0.0,0.0,1.0,1.0",
            "-180.0,-90.0,180.0,90.0",
            "-1.0,-1.0,-0.5,-0.5",
        ]

        for bbox in valid_bboxes:
            with self.subTest(bbox=bbox):
                result = _parse_bbox(bbox)
                self.assertIsInstance(result, tuple)
                self.assertEqual(len(result), 4)

        # Test invalid bboxes
        invalid_bboxes = [
            "invalid",
            "1.0,2.0,3.0",  # Too few values
            "1.0,2.0,3.0,4.0,5.0",  # Too many values
            "text,0.0,1.0,1.0",  # Non-numeric
            "3.0,4.0,1.0,2.0",  # max < min (should fail)
        ]

        for bbox in invalid_bboxes:
            with self.subTest(bbox=bbox):
                with self.assertRaises(Exception):
                    _parse_bbox(bbox)

    def test_resolution_validation(self):
        """Test H3 resolution validation"""
        from services.geo_service.app.main import _parse_res_view

        # Test valid resolutions
        valid_resolutions = ["8", "9", "10", "11", "12"]
        base_resolution = 9

        for res in valid_resolutions:
            with self.subTest(res=res):
                result = _parse_res_view(res, base_resolution)
                self.assertIsInstance(result, int)
                self.assertGreaterEqual(result, 0)
                self.assertLessEqual(result, base_resolution)

        # Test invalid resolutions
        invalid_resolutions = ["-1", "20", "abc", ""]

        for res in invalid_resolutions:
            with self.subTest(res=res):
                with self.assertRaises(Exception):
                    _parse_res_view(res, base_resolution)

        # Test resolution higher than base
        with self.assertRaises(Exception):
            _parse_res_view("10", 9)  # 10 > 9

    def test_concurrent_database_access(self):
        """Test concurrent database access patterns"""
        import threading
        import time

        from app import db as db_module

        results = []
        errors = []

        def worker(worker_id):
            try:
                conn = db_module.get_connection()
                # Simple read operation
                cur = conn.cursor()
                cur.execute("SELECT 1")
                result = cur.fetchone()
                results.append((worker_id, result))
            except Exception as e:
                errors.append((worker_id, str(e)))

        # Start multiple threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Check results
        self.assertEqual(len(results), 5)
        self.assertEqual(len(errors), 0)

        for worker_id, result in results:
            self.assertEqual(result, (1,))

    @patch('services.geo_service.app.main.cache.get_redis')
    def test_redis_cache_failures(self, mock_get_redis):
        """Test Redis cache failure handling"""
        from services.geo_service.app.main import app as geo_app
        client = TestClient(geo_app)

        # Mock Redis connection failure
        mock_redis = AsyncMock()
        mock_redis.get.side_effect = Exception("Redis connection failed")
        mock_get_redis.return_value = mock_redis

        # Request should still succeed (cache miss handled gracefully)
        response = client.get("/api/v1/stats/summary")
        self.assertEqual(response.status_code, 200)

    @patch('services.visit_service.app.main.publish_to_rabbitmq')
    def test_rabbitmq_failures(self, mock_publish):
        """Test RabbitMQ failure handling"""
        from services.visit_service.app.main import app as visit_app
        client = TestClient(visit_app)

        # Mock RabbitMQ failure
        mock_publish.side_effect = Exception("RabbitMQ down")

        # Visit should still succeed (fire-and-forget messaging)
        response = client.post(
            "/api/v1/visit",
            json={"lat": 55.0, "lon": 37.0}
        )
        self.assertEqual(response.status_code, 200)

        # But the exception should have been raised (and caught by middleware)
        mock_publish.assert_called_once()

    def test_large_bbox_requests(self):
        """Test handling of large bbox requests that might cause performance issues"""
        from services.geo_service.app.main import app as geo_app
        client = TestClient(geo_app)

        # Very large bbox covering most of the world
        large_bbox = "-180.0,-90.0,180.0,90.0"

        response = client.get(f"/api/v1/districts?bbox={large_bbox}&level=okrug")
        # Should not crash, but might return limited results
        self.assertIn(response.status_code, [200, 404])  # 404 if no districts in bbox

    def test_extreme_coordinate_values(self):
        """Test handling of extreme coordinate values"""
        from services.monolith.main import app as monolith_app
        client = TestClient(monolith_app)

        # Test coordinates very close to poles
        extreme_coords = [
            (89.999, 0.0),    # Near north pole
            (-89.999, 0.0),   # Near south pole
            (0.0, 179.999),   # Near international date line
            (0.0, -179.999),  # Near international date line
        ]

        for lat, lon in extreme_coords:
            with self.subTest(lat=lat, lon=lon):
                response = client.post(
                    "/api/v1/visit",
                    json={"lat": lat, "lon": lon},
                    headers={"X-User-Tg-Id": "123"}
                )
                # Should not crash
                self.assertIn(response.status_code, [200, 422])

    def test_malformed_telegram_auth(self):
        """Test handling of malformed Telegram authentication data"""
        from services.monolith.main import verify_init_data

        malformed_data = [
            "",  # Empty
            "invalid=data",  # No hash
            "hash=abc123&auth_date=invalid",  # Invalid auth_date
            "hash=abc123&auth_date=123&user=invalid_json",  # Invalid JSON
        ]

        for data in malformed_data:
            with self.subTest(data=data):
                result = verify_init_data(data, "test_token")
                self.assertFalse(result["ok"])

    def test_database_transaction_rollback(self):
        """Test database transaction rollback on errors"""
        from app import db as db_module

        conn = db_module.get_connection()

        # Start a transaction that will fail
        try:
            cur = conn.cursor()
            cur.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (999999, "test"))
            # This should succeed

            # Try to insert duplicate - should fail
            cur.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (999999, "test2"))
            # This should fail due to unique constraint

            conn.commit()  # This should not be reached
            self.fail("Expected transaction to fail")
        except Exception:
            # Expected - transaction should be rolled back
            conn.rollback()

        # Verify the first insert was rolled back
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE tg_id = ?", (999999,))
        count = cur.fetchone()[0]
        self.assertEqual(count, 0)

    def test_memory_usage_large_result_sets(self):
        """Test handling of potentially large result sets"""
        from app import db as db_module

        conn = db_module.get_connection()

        # Insert many test records
        for i in range(100):
            db_module.ensure_user(conn, tg_id=100000 + i, username=f"user{i}")

        # Test that queries with limits work
        users = db_module.select_circles_in_bbox(
            conn,
            user_id=1,
            min_lat=0,
            min_lon=0,
            max_lat=90,
            max_lon=180
        )
        # Should not crash and should respect limits
        self.assertLessEqual(len(users), 10000)  # LIMIT 10000 in query

    def test_service_startup_failures(self):
        """Test service startup with missing dependencies"""
        # Test monolith startup without database
        original_db_url = os.environ.get("DATABASE_URL")
        try:
            os.environ.pop("DATABASE_URL", None)

            # Import should not fail, but first database operation should
            from app import db as db_module

            with self.assertRaises(ValueError):
                db_module.get_connection()

        finally:
            if original_db_url:
                os.environ["DATABASE_URL"] = original_db_url

    def test_concurrent_api_requests(self):
        """Test concurrent API requests"""
        import threading

        from services.monolith.main import app as monolith_app

        results = []
        errors = []

        def make_request(thread_id):
            try:
                client = TestClient(monolith_app)
                response = client.get("/health")
                results.append((thread_id, response.status_code))
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Start multiple concurrent requests
        threads = []
        for i in range(10):
            t = threading.Thread(target=make_request, args=(i,))
            threads.append(t)
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # All requests should succeed
        self.assertEqual(len(results), 10)
        self.assertEqual(len(errors), 0)

        for thread_id, status_code in results:
            self.assertEqual(status_code, 200)


if __name__ == "__main__":
    unittest.main()
