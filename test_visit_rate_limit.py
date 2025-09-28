#!/usr/bin/env python3
"""
Test script for visit endpoint rate limiting
"""
import sys
import os
import asyncio

# Add services to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services'))

async def test_visit_rate_limit():
    """Test visit endpoint with rate limiting"""
    print("Testing visit endpoint rate limiting...")

    # Set up environment first
    os.environ["DATABASE_URL"] = "sqlite:///test_rate_limit.db"
    os.environ["NO_AUTH_MODE"] = "1"
    # Don't set REDIS_URL to test graceful degradation

    # Now import after environment is set
    from monolith.main import app
    from fastapi.testclient import TestClient

    # Create test database
    from app import db as db_module
    conn = db_module.get_connection()
    db_module.init_db(conn)

    # Seed with minimal test data
    conn.executescript("""
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
            total_cells INTEGER,
            total_weight REAL
        );

        INSERT OR IGNORE INTO districts (id, level, name_ru, parent_id, geom_geojson,
                                       bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                                       total_cells, total_weight)
        VALUES (1, 'district', 'Test District', NULL,
               '{"type":"Polygon","coordinates":[[[37.0,55.0],[38.0,55.0],[38.0,56.0],[37.0,56.0],[37.0,55.0]]]}',
               37.0, 55.0, 38.0, 56.0, 100, 10.0);

        CREATE TABLE IF NOT EXISTS district_cells (
            district_id INTEGER,
            h3_index TEXT,
            coverage REAL,
            PRIMARY KEY (district_id, h3_index)
        );

        INSERT OR IGNORE INTO district_cells (district_id, h3_index, coverage)
        VALUES (1, '831c2dfffffffff', 0.8);

        CREATE TABLE IF NOT EXISTS visits (
            user_id INTEGER,
            h3_index TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, h3_index)
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            tg_id INTEGER UNIQUE,
            username TEXT
        );
    """)
    conn.commit()

    client = TestClient(app)

    try:
        # Test graceful degradation without Redis
        print("Testing graceful degradation without Redis...")
        for i in range(25):  # Make more than 20 requests
            response = client.post(
                "/api/v1/visit",
                json={"lat": 55.5, "lon": 37.5},
                headers={"X-User-Tg-Id": "12345", "X-User-Username": "testuser"}
            )
            if response.status_code != 200:
                print(f"❌ Request {i+1} failed with status {response.status_code}: {response.text}")
                return False

        print("✅ Graceful degradation test passed! All requests allowed without Redis")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_visit_rate_limit())
    sys.exit(0 if success else 1)
