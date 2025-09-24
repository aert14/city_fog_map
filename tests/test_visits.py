import os
import sqlite3
import tempfile
import unittest

from app import db


class VisitStatsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.sqlite3")
        os.environ["DB_PATH"] = self.db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        db.init_db(self.conn)
        self._ensure_geo_tables()
        self._seed_districts()

        self.user_id = db.ensure_user(self.conn, tg_id=1, username="user")

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
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'okrug', ?, NULL, '{}', -1.0, -1.0, 1.0, 1.0, 1, 1.0)
            """,
            (10, "Test Okrug"),
        )
        self.conn.execute(
            """
            INSERT INTO districts (
                id, level, name_ru, parent_id,
                geom_geojson,
                bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                total_cells, total_weight
            ) VALUES (?, 'district', ?, ?, '{}', -0.5, -0.5, 0.5, 0.5, 1, 1.1)
            """,
            (100, "Test District", 10),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "866ffffffffffff", 0.8),
        )
        self.conn.execute(
            "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
            (100, "8663fffffffffff", 0.3),
        )
        self.conn.commit()

    def test_fetch_districts_in_bbox_progress(self) -> None:
        rows = db.fetch_districts_in_bbox(
            self.conn,
            user_id=self.user_id,
            min_lon=-1.0,
            min_lat=-1.0,
            max_lon=1.0,
            max_lat=1.0,
            level="district",
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], 100)
        self.assertEqual(row["user_visited_cells"], 0)

        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=0,
        )

        rows = db.fetch_districts_in_bbox(
            self.conn,
            user_id=self.user_id,
            min_lon=-1.0,
            min_lat=-1.0,
            max_lon=1.0,
            max_lat=1.0,
            level="district",
        )
        self.assertEqual(rows[0]["user_visited_cells"], 1)

    def test_progress_summary_helpers(self) -> None:
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=0,
        )
        totals = db.fetch_user_total_progress(self.conn, user_id=self.user_id)
        self.assertEqual(totals["visited_cells"], 1)
        self.assertEqual(totals["total_cells"], 1)

        okrugs = db.fetch_user_okrug_progress(self.conn, user_id=self.user_id)
        self.assertEqual(len(okrugs), 1)
        okrug_row = okrugs[0]
        self.assertEqual(okrug_row["visited_cells"], 1)
        self.assertEqual(okrug_row["total_cells"], 1)

        bottom = db.fetch_user_bottom_districts(self.conn, user_id=self.user_id, limit=3)
        self.assertEqual(len(bottom), 1)
        bottom_row = bottom[0]
        self.assertEqual(bottom_row["id"], 100)
        self.assertEqual(bottom_row["visited_cells"], 1)

    def test_fetch_district_cells_and_visits(self) -> None:
        cells = db.fetch_district_cells(self.conn, 100)
        self.assertEqual(len(cells), 2)
        visits = db.fetch_user_visited_cells_for_district(
            self.conn, user_id=self.user_id, district_id=100
        )
        self.assertEqual(visits, [])

        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=0,
        )

        visits_after = db.fetch_user_visited_cells_for_district(
            self.conn, user_id=self.user_id, district_id=100
        )
        self.assertEqual(visits_after, ["866ffffffffffff"])

    def test_first_visit_updates_stats(self) -> None:
        added = db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=0,
        )
        self.assertTrue(added)

        stats = db.fetch_user_stats(self.conn, user_id=self.user_id, district_id=100, okrug_id=10)
        self.assertEqual(stats["total_circles"], 1)
        self.assertEqual(stats["district"]["visited_cells"], 1)
        self.assertAlmostEqual(stats["district"]["visited_weight"], 0.8)
        self.assertEqual(stats["okrug"]["visited_cells"], 1)
        self.assertAlmostEqual(stats["okrug"]["visited_weight"], 0.8)

    def test_second_visit_same_cell_ignored(self) -> None:
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=0,
        )
        added = db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=1,
        )
        self.assertFalse(added)

        stats = db.fetch_user_stats(self.conn, user_id=self.user_id, district_id=100, okrug_id=10)
        self.assertEqual(stats["total_circles"], 1)
        self.assertEqual(stats["district"]["visited_cells"], 1)
        self.assertAlmostEqual(stats["district"]["visited_weight"], 0.8)

    def test_low_coverage_counts_only_weight(self) -> None:
        added = db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="8663fffffffffff",
            district_id=100,
            coverage=0.3,
            okrug_id=10,
            now_ts=2,
        )
        self.assertTrue(added)

        stats = db.fetch_user_stats(self.conn, user_id=self.user_id, district_id=100, okrug_id=10)
        self.assertEqual(stats["total_circles"], 1)
        self.assertEqual(stats["district"]["visited_cells"], 0)
        self.assertAlmostEqual(stats["district"]["visited_weight"], 0.3)

    def test_delete_visit_updates_stats(self) -> None:
        db.record_visit_and_increment_stats(
            self.conn,
            user_id=self.user_id,
            h3_index="866ffffffffffff",
            district_id=100,
            coverage=0.8,
            okrug_id=10,
            now_ts=0,
        )
        deleted = db.delete_visit_by_hex(
            self.conn, user_id=self.user_id, h3_index="866ffffffffffff"
        )
        self.assertEqual(deleted, 1)

        stats = db.fetch_user_stats(self.conn, user_id=self.user_id, district_id=100, okrug_id=10)
        self.assertEqual(stats["total_circles"], 0)
        if stats["district"]:
            self.assertEqual(stats["district"]["visited_cells"], 0)
            self.assertAlmostEqual(stats["district"]["visited_weight"], 0.0)


if __name__ == "__main__":
    unittest.main()
