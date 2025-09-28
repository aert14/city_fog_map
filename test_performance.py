#!/usr/bin/env python3
"""
Performance test to demonstrate the geometry optimization benefits
"""

import os
import sys
import time
import h3
import psycopg2

# Add the services/common directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services', 'common'))

def test_performance():
    """Test performance of the optimized geometry queries"""

    DATABASE_URL = 'postgresql://user:password@localhost:5432/gis'

    try:
        conn = psycopg2.connect(DATABASE_URL)

        # Create a test user
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE tg_id = %s", (987654321,))
            row = cur.fetchone()
            if row:
                user_id = row[0]
                # Clean up previous test data
                cur.execute("DELETE FROM user_visits_atomic WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM user_district_stats WHERE user_id = %s", (user_id,))
                cur.execute("DELETE FROM user_okrug_stats WHERE user_id = %s", (user_id,))
            else:
                cur.execute("INSERT INTO users (tg_id, username) VALUES (%s, %s) RETURNING id", (987654321, "perf_test_user"))
                user_id = cur.fetchone()[0]
            conn.commit()

        print(f"Using test user ID: {user_id}")

        # Generate many h3 indices around Moscow
        moscow_center_lat, moscow_center_lon = 55.7558, 37.6176
        center_cell = h3.latlng_to_cell(moscow_center_lat, moscow_center_lon, 10)

        # Generate a large number of cells (simulate user with many visits)
        all_cells = []
        for ring in range(0, 8):  # This will create hundreds of cells
            all_cells.extend(h3.grid_ring(center_cell, ring))

        print(f"Generated {len(all_cells)} test h3 cells")

        # Insert visits (this will populate the geom column)
        inserted = 0
        start_time = time.time()
        with conn.cursor() as cur:
            for i, h3_index in enumerate(all_cells):
                lat, lon = h3.cell_to_latlng(h3_index)
                cur.execute(
                    """
                    INSERT INTO user_visits_atomic(user_id, h3, ts, geom)
                    VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                    ON CONFLICT DO NOTHING
                    """,
                    (user_id, h3_index, int(start_time) + i, lon, lat),
                )
                inserted += cur.rowcount
        conn.commit()
        insert_time = time.time() - start_time
        print(f"Inserted {inserted} visits in {insert_time:.2f} seconds")

        # Test bbox query performance
        bbox_queries = [
            # Large bbox (should return many results)
            (55.5, 37.3, 56.0, 37.9),
            # Medium bbox
            (55.7, 37.5, 55.8, 37.7),
            # Small bbox
            (55.75, 37.6, 55.77, 37.62),
        ]

        for i, (min_lat, min_lon, max_lat, max_lon) in enumerate(bbox_queries):
            print(f"\nTesting bbox query {i+1}: lat[{min_lat:.2f}-{max_lat:.2f}], lon[{min_lon:.2f}-{max_lon:.2f}]")

            # Time the optimized query
            start_time = time.time()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM user_visits_atomic
                    WHERE user_id = %s
                      AND geom IS NOT NULL
                      AND ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                    """,
                    (user_id, min_lon, min_lat, max_lon, max_lat),
                )
                count = cur.fetchone()[0]
            query_time = time.time() - start_time

            print(f"  Found {count} hexes in {query_time:.4f} seconds")

        # Clean up
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_visits_atomic WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM user_district_stats WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM user_okrug_stats WHERE user_id = %s", (user_id,))
        conn.commit()

        print("\nPerformance test completed successfully!")
        print("The optimized geometry queries with spatial index should be very fast even with thousands of visits.")

    except Exception as e:
        print(f"Performance test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    success = test_performance()
    sys.exit(0 if success else 1)
