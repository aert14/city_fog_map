#!/usr/bin/env python3
"""
Simple test to verify that the moved Pydantic models work correctly
and can generate OpenAPI schemas for Swagger documentation.
"""

import sys
import os
sys.path.append('services')

try:
    from common import models
    from fastapi import FastAPI

    # Test that models can be instantiated
    visit_request = models.VisitRequest(lat=55.7558, lon=37.6173)
    circle = models.Circle(lat=55.7558, lon=37.6173)
    visit_stats = models.VisitStats(total_circles=1)
    visit_response = models.VisitResponse(added=1, circle=circle, stats=visit_stats)
    circles_response = models.CirclesResponse(hexagons=["test_hex"])

    print("✓ Models can be instantiated")

    # Create a minimal FastAPI app to test the models
    app = FastAPI(title="Test API", version="1.0.0")

    @app.post("/test-visit", response_model=models.VisitResponse)
    async def test_visit(body: models.VisitRequest):
        return visit_response

    @app.get("/test-circles", response_model=models.CirclesResponse)
    async def test_circles():
        return circles_response

    # Test OpenAPI schema generation
    schema = app.openapi()
    assert "VisitResponse" in str(schema)
    assert "VisitRequest" in str(schema)
    assert "CirclesResponse" in str(schema)
    print("✓ OpenAPI schema generation works")

    # Test that response_model is correctly set
    visit_route = None
    circles_route = None
    for route in app.routes:
        if hasattr(route, 'path') and route.path == "/test-visit":
            visit_route = route
        elif hasattr(route, 'path') and route.path == "/test-circles":
            circles_route = route

    assert visit_route is not None
    assert circles_route is not None
    print("✓ Routes are properly registered")

    print("All tests passed! Models are working correctly and Swagger docs should generate properly.")

except Exception as e:
    print(f"Test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)