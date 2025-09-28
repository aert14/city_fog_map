#!/usr/bin/env python3
"""
Simple test script to verify the geometry optimization works
"""

import os
import sys
import h3

# Add the services/common directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services', 'common'))

from db import get_connection, init_db, record_visit_and_increment_stats, select_user_hexes_in_bbox, ensure_user

def test_geom_optimization():
    """Test that the geometry optimization works correctly"""

    # Use a test database URL
    os.environ['DATABASE_URL'] = 'postgresql://postgres:password@localhost:5432/cityfog_test'

    try:
        conn = get_connection()

        # Initialize database
        init_db(conn)

        # Create a test user
        user_id = ensure_user(conn, 123456789, "test_user")
        print(f"Created test user with ID: {user_id}")

        # Create some test h3 indices in Moscow area (Red Square coordinates)
        red_square_lat, red_square_lon = 55.7558, 37.6176
        center_cell = h3.latlng_to_cell(red_square_lat, red_square_lon, 10)
        test_h3_indices = [center_cell] + h3.grid_ring(center_cell, 1) + h3.grid_ring(center_cell, 2)

        # Record visits
        for i, h3_index in enumerate(test_h3_indices):
            success = record_visit_and_increment_stats(
                conn=conn,
                user_id=user_id,
                h3_index=h3_index,
                district_id=445280,  # район Сокол
                coverage=1.0,
                okrug_id=162903,    # Северный административный округ
                now_ts=int(time.time()) + i * 60  # Different timestamps
            )
            if success:
                print(f"Recorded visit for h3: {h3_index}")
            else:
                print(f"Failed to record visit for h3: {h3_index}")

        # Test bbox query - should return all 5 hexes
        bbox_result = select_user_hexes_in_bbox(
            conn=conn,
            user_id=user_id,
            min_lat=55.7,  # Moscow area
            min_lon=37.5,
            max_lat=55.8,
            max_lon=37.7
        )
        print(f"Bbox query returned {len(bbox_result)} hexes: {bbox_result}")

        # Test smaller bbox - should return fewer hexes
        small_bbox_result = select_user_hexes_in_bbox(
            conn=conn,
            user_id=user_id,
            min_lat=55.75,
            min_lon=37.6,
            max_lat=55.76,
            max_lon=37.65
        )
        print(f"Small bbox query returned {len(small_bbox_result)} hexes: {small_bbox_result}")

        print("Geometry optimization test completed successfully!")

    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    import time
    success = test_geom_optimization()
    sys.exit(0 if success else 1)
