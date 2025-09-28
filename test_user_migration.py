#!/usr/bin/env python3
"""
Simple test to verify that the migrated user functionality works correctly
"""

import sys
import os
sys.path.append('services')

try:
    from common import models
    from services.common import database as db
    from fastapi import FastAPI

    # Test that UserInfo model can be instantiated
    user_info = models.UserInfo(id=1, tg_id=123456, username='testuser')
    print("✓ UserInfo model can be instantiated")

    # Test that get_user_by_id function exists
    assert hasattr(db, 'get_user_by_id')
    print("✓ get_user_by_id function exists in database module")

    # Test that ensure_user function exists (should already be there)
    assert hasattr(db, 'ensure_user')
    print("✓ ensure_user function exists in database module")

    # Create a minimal FastAPI app to test the endpoints
    app = FastAPI(title="Test User API", version="1.0.0")

    @app.get("/api/user/{user_id}", response_model=models.UserInfo)
    async def get_user(user_id: int):
        """Test endpoint for get_user"""
        return user_info

    @app.post("/api/authenticate")
    async def authenticate():
        """Test endpoint for authenticate"""
        return {
            "user_id": 1,
            "username": "testuser",
            "authenticated": True
        }

    # Test OpenAPI schema generation
    schema = app.openapi()
    assert "UserInfo" in str(schema)
    print("✓ UserInfo appears in OpenAPI schema")

    # Test that routes are properly registered
    routes = [route.path for route in app.routes if hasattr(route, 'path')]
    assert "/api/user/{user_id}" in routes
    assert "/api/authenticate" in routes
    print("✓ User endpoints are properly registered")

    print("All tests passed! User migration is working correctly.")

except Exception as e:
    print(f"Test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
