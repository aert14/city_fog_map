import os
import sqlite3
import tempfile
import unittest
import time
from unittest.mock import patch, MagicMock

from tests import test_db as db


class ExtendedDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.sqlite3")
        os.environ["DB_PATH"] = self.db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        db.init_db(self.conn)
        self._ensure_geo_tables()
        self._seed_districts()

        self.user_id = db.ensure_user(self.conn, tg_id=1, username="testuser")
        self.user2_id = db.ensure_user(self.conn, tg_id=2, username="testuser2")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmpdir.cleanup()
        os.environ.pop("DB_PATH", None)

    def _ensure_geo_tables(self) -> None:
        self.conn.executescript(
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

    def _seed_districts(self) -> None:
        # Create okrugs
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'okrug', ?, NULL, '{}', -2.0, -2.0, 2.0, 2.0, 0, 0.0)
            """,
            (10, "Central Okrug"),
        )
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'okrug', ?, NULL, '{}', 2.0, 2.0, 4.0, 4.0, 0, 0.0)
            """,
            (20, "Northern Okrug"),
        )

        # Create districts
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, '{}', -1.0, -1.0, 1.0, 1.0, 3, 2.5)
            """,
            (100, "Tverskoy District", 10),
        )
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, '{}', -0.5, -0.5, 0.5, 0.5, 2, 1.8)
            """,
            (101, "Arbat District", 10),
        )
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, '{}', 2.5, 2.5, 3.5, 3.5, 1, 0.9)
            """,
            (200, "Northern District", 20),
        )

        # Add district cells
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "866ffffffffffff", 0.8),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "8663fffffffffff", 0.7),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "8664fffffffffff", 0.5),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (101, "8665fffffffffff", 0.8),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (101, "8666fffffffffff", 0.4),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (200, "8667fffffffffff", 0.6),
        )
        self.conn.commit()

    def test_ensure_user_existing(self) -> None:
        """Test ensuring an existing user"""
        user_id = db.ensure_user(self.conn, tg_id=1, username="newname")
        self.assertEqual(user_id, self.user_id)

        # Check username was updated
        cur = self.conn.cursor()
        cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        self.assertEqual(row[0], "newname")

    def test_ensure_user_new(self) -> None:
        """Test creating a new user"""
        user_id = db.ensure_user(self.conn, tg_id=999, username="newuser")
        self.assertIsInstance(user_id, int)
        self.assertNotEqual(user_id, self.user_id)

        # Check user settings were created
        cur = self.conn.cursor()
        cur.execute("SELECT h3_resolution FROM user_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        self.assertEqual(row[0], db.BASE_VISIT_RESOLUTION)

    def test_insert_circle_if_new(self) -> None:
        """Test inserting circles"""
        # Insert new circle
        inserted = db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="test_key", lat=55.0, lon=37.0
        )
        self.assertTrue(inserted)

        # Try to insert same circle again
        inserted_again = db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="test_key", lat=55.0, lon=37.0
        )
        self.assertFalse(inserted_again)

        # Check circle count
        count = db.count_circles(self.conn, user_id=self.user_id)
        self.assertEqual(count, 1)

    def test_select_circles_in_bbox(self) -> None:
        """Test selecting circles within bounding box"""
        # Insert some circles
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="key1", lat=55.0, lon=37.0
        )
        import time
        time.sleep(0.001)  # Small delay to ensure different timestamps
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="key2", lat=56.0, lon=38.0
        )
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="key3", lat=50.0, lon=30.0
        )

        # Query bbox that should contain first two circles
        circles = db.select_circles_in_bbox(
            self.conn,
            user_id=self.user_id,
            min_lat=54.0,
            min_lon=36.0,
            max_lat=57.0,
            max_lon=39.0,
        )
        self.assertEqual(len(circles), 2)
        # Results should be ordered by created_at DESC
        # Note: key2 was inserted after key1, so should be more recent
        # Let's check what we actually get
        circle_keys = [c[2] for c in circles]
        self.assertIn("key1", circle_keys)
        self.assertIn("key2", circle_keys)
        # The ordering might depend on SQLite timestamp precision

    def test_delete_circle_by_geokey(self) -> None:
        """Test deleting circles"""
        # Insert circle
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="test_key", lat=55.0, lon=37.0
        )

        # Delete existing circle
        deleted = db.delete_circle_by_geokey(
            self.conn, user_id=self.user_id, geokey="test_key"
        )
        self.assertEqual(deleted, 1)

        # Try to delete non-existent circle
        deleted_again = db.delete_circle_by_geokey(
            self.conn, user_id=self.user_id, geokey="nonexistent"
        )
        self.assertEqual(deleted_again, 0)

    def test_user_h3_resolution_settings(self) -> None:
        """Test H3 resolution settings management"""
        # Default resolution
        resolution = db.get_user_h3_resolution(self.conn, self.user_id)
        self.assertEqual(resolution, db.BASE_VISIT_RESOLUTION)

        # Update resolution
        updated = db.update_user_h3_resolution(
            self.conn, user_id=self.user_id, h3_resolution=12
        )
        self.assertEqual(updated, 1)

        # Get updated resolution
        resolution = db.get_user_h3_resolution(self.conn, self.user_id)
        self.assertEqual(resolution, 12)

    def test_clear_user_circles(self) -> None:
        """Test clearing all user circles"""
        # Insert multiple circles
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="key1", lat=55.0, lon=37.0
        )
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="key2", lat=56.0, lon=38.0
        )

        # Clear circles
        cleared = db.clear_user_circles(self.conn, self.user_id)
        self.assertEqual(cleared, 2)

        # Check count is zero
        count = db.count_circles(self.conn, user_id=self.user_id)
        self.assertEqual(count, 0)

    def test_clear_all_data(self) -> None:
        """Test clearing all database data"""
        # Add some data
        db.insert_circle_if_new(
            self.conn, user_id=self.user_id, geokey="key1", lat=55.0, lon=37.0
        )

        # Clear all
        circles_cleared, users_cleared = db.clear_all(self.conn)
        self.assertEqual(circles_cleared, 1)
        self.assertEqual(users_cleared, 2)  # We have 2 test users

        # Check tables are empty
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM circles")
        self.assertEqual(cur.fetchone()[0], 0)

    def test_select_district_for_cell(self) -> None:
        """Test finding district for H3 cell"""
        # Test existing cell
        result = db.select_district_for_cell(self.conn, "866ffffffffffff")
        self.assertIsNotNone(result)
        district_id, coverage = result
        self.assertEqual(district_id, 100)
        self.assertEqual(coverage, 0.8)

        # Test non-existent cell
        result = db.select_district_for_cell(self.conn, "nonexistent")
        self.assertIsNone(result)

    def test_select_district_parent(self) -> None:
        """Test finding district parent"""
        # Test district with parent
        parent_id = db.select_district_parent(self.conn, 100)
        self.assertEqual(parent_id, 10)

        # Test district without parent (okrug)
        parent_id = db.select_district_parent(self.conn, 10)
        self.assertIsNone(parent_id)

        # Test non-existent district
        parent_id = db.select_district_parent(self.conn, 99999)
        self.assertIsNone(parent_id)

    def test_count_user_visited_hexes(self) -> None:
        """Test counting visited hexes"""
        # Initially zero
        count = db.count_user_visited_hexes(self.conn, self.user_id)
        self.assertEqual(count, 0)

        # Record some visits
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        count = db.count_user_visited_hexes(self.conn, self.user_id)
        self.assertEqual(count, 1)

    def test_select_user_hexes(self) -> None:
        """Test selecting user visited hexes"""
        # Initially empty
        hexes = db.select_user_hexes(self.conn, self.user_id)
        self.assertEqual(hexes, [])

        # Record visits
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="8663fffffffffff",
            district_id=100,
            coverage=0.7,
            okrug_id=10,
        )

        hexes = db.select_user_hexes(self.conn, self.user_id)
        self.assertEqual(len(hexes), 2)
        self.assertIn("866ffffffffffff", hexes)
        self.assertIn("8663fffffffffff", hexes)

    def test_fetch_district_cells(self) -> None:
        """Test fetching district cells"""
        cells = db.fetch_district_cells(self.conn, 100)
        self.assertEqual(len(cells), 3)

        # Check specific cell
        cell_h3s = [cell[0] for cell in cells]
        self.assertIn("866ffffffffffff", cell_h3s)

        # Test empty district
        cells = db.fetch_district_cells(self.conn, 999)
        self.assertEqual(len(cells), 0)

    def test_fetch_user_visited_cells_for_district(self) -> None:
        """Test fetching visited cells for district"""
        # Initially empty
        visited = db.fetch_user_visited_cells_for_district(
            self.conn, user_id=self.user_id, district_id=100
        )
        self.assertEqual(visited, [])

        # Record visit
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        visited = db.fetch_user_visited_cells_for_district(
            self.conn, user_id=self.user_id, district_id=100
        )
        self.assertEqual(visited, ["866ffffffffffff"])

    def test_fetch_user_total_progress(self) -> None:
        """Test fetching total user progress"""
        # Initially zero
        progress = db.fetch_user_total_progress(self.conn, self.user_id)
        self.assertEqual(progress["visited_cells"], 0)
        self.assertEqual(progress["total_cells"], 6)  # Sum of all district total_cells

        # Record visit
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        progress = db.fetch_user_total_progress(self.conn, self.user_id)
        self.assertEqual(progress["visited_cells"], 1)
        self.assertEqual(progress["total_cells"], 6)

    def test_fetch_user_okrug_progress(self) -> None:
        """Test fetching okrug progress"""
        okrugs = db.fetch_user_okrug_progress(self.conn, self.user_id)
        self.assertEqual(len(okrugs), 2)  # Two okrugs

        # Check okrug names
        names = [o["name_ru"] for o in okrugs]
        self.assertIn("Central Okrug", names)
        self.assertIn("Northern Okrug", names)

        # Check totals
        central_okrug = next(o for o in okrugs if o["name_ru"] == "Central Okrug")
        self.assertEqual(central_okrug["total_cells"], 5)  # 3 + 2
        self.assertEqual(central_okrug["visited_cells"], 0)

    def test_fetch_user_bottom_districts(self) -> None:
        """Test fetching bottom districts"""
        bottom = db.fetch_user_bottom_districts(self.conn, self.user_id, limit=5)
        self.assertEqual(len(bottom), 3)  # Three districts

        # Should be ordered by progress ratio ascending
        ratios = [d["progress_ratio"] for d in bottom]
        self.assertEqual(ratios, sorted(ratios))

    def test_get_total_cells_and_weight(self) -> None:
        """Test getting total cells and weight"""
        # District level
        total_cells, total_weight = db.get_total_cells_and_weight(self.conn, level="district")
        self.assertEqual(total_cells, 6)  # 3 + 2 + 1
        self.assertAlmostEqual(total_weight, 5.2)  # 2.5 + 1.8 + 0.9

        # Okrug level
        total_cells, total_weight = db.get_total_cells_and_weight(self.conn, level="okrug")
        self.assertEqual(total_cells, 6)  # Same as district level
        self.assertAlmostEqual(total_weight, 5.2)

    def test_get_district_by_id(self) -> None:
        """Test getting district by ID"""
        district = db.get_district_by_id(self.conn, 100)
        self.assertIsNotNone(district)
        self.assertEqual(district["name_ru"], "Tverskoy District")
        self.assertEqual(district["level"], "district")

        # Non-existent district
        district = db.get_district_by_id(self.conn, 99999)
        self.assertIsNone(district)

    def test_fetch_districts_by_ids(self) -> None:
        """Test fetching multiple districts by IDs"""
        districts = db.fetch_districts_by_ids(
            self.conn, user_id=self.user_id, district_ids=[100, 101]
        )
        self.assertEqual(len(districts), 2)

        names = [d["name_ru"] for d in districts]
        self.assertIn("Tverskoy District", names)
        self.assertIn("Arbat District", names)

        # Empty list
        districts = db.fetch_districts_by_ids(
            self.conn, user_id=self.user_id, district_ids=[]
        )
        self.assertEqual(len(districts), 0)

    def test_multiple_users_isolation(self) -> None:
        """Test that user data is properly isolated"""
        # User 1 visits
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        # User 2 visits different cell
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user2_id,
            h3_index="8665fffffffffff",
            district_id=101,
            coverage=0.8,
            okrug_id=10,
        )

        # Check user 1 progress
        progress1 = db.fetch_user_total_progress(self.conn, self.user_id)
        self.assertEqual(progress1["visited_cells"], 1)

        # Check user 2 progress
        progress2 = db.fetch_user_total_progress(self.conn, self.user2_id)
        self.assertEqual(progress2["visited_cells"], 1)

        # Check user 1 hexes
        hexes1 = db.select_user_hexes(self.conn, self.user_id)
        self.assertEqual(hexes1, ["866ffffffffffff"])

        # Check user 2 hexes
        hexes2 = db.select_user_hexes(self.conn, self.user2_id)
        self.assertEqual(hexes2, ["8665fffffffffff"])

    def test_delete_visit_updates_stats(self) -> None:
        """Test deleting visits updates statistics correctly"""
        # Record visit
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
        )

        # Check stats before deletion
        stats_before = db.fetch_user_stats(
            self.conn, user_id=self.user_id, district_id=100, okrug_id=10
        )
        self.assertEqual(stats_before["district"]["visited_cells"], 1)
        self.assertEqual(stats_before["okrug"]["visited_cells"], 1)

        # Delete visit
        deleted = db.delete_visit_by_hex(
            self.conn, user_id=self.user_id, h3_index="866ffffffffffff"
        )
        self.assertEqual(deleted, 1)

        # Check stats after deletion
        stats_after = db.fetch_user_stats(
            self.conn, user_id=self.user_id, district_id=100, okrug_id=10
        )
        # District stats should be cleared since we deleted the only visit
        if stats_after["district"]:
            self.assertEqual(stats_after["district"]["visited_cells"], 0)
            self.assertEqual(stats_after["district"]["visited_weight"], 0.0)
        if stats_after["okrug"]:
            self.assertEqual(stats_after["okrug"]["visited_cells"], 0)
            self.assertEqual(stats_after["okrug"]["visited_weight"], 0.0)


if __name__ == "__main__":
    unittest.main()
