# Geo Service - Archived

This microservice has been archived as part of the monolith migration.

## What was moved to monolith

All geo-related functionality has been integrated into the monolith service:
- District and okrug data retrieval
- User progress statistics and summaries
- Leaderboard calculations
- District cell information and visit tracking
- Circle/hexagon data management
- Redis caching for performance optimization

## Original functionality

This service previously handled:
- GET /api/v1/districts - List districts and okrugs with user progress
- GET /api/v1/districts/all - Get all districts with progress
- GET /api/v1/district/{id}/cells - Get detailed cell information for districts
- GET /api/v1/circles - User circles/hexagons in bounding box
- GET /api/v1/stats/summary - User progress summary with caching
- GET /api/v1/leaderboard - Leaderboard with time-based filtering
- POST /api/v1/district/{id}/reveal - Debug endpoint for revealing districts

## Migration notes

- All database operations moved to `services/common/db.py`
- Redis caching logic moved to monolith service
- Authentication logic preserved in monolith
- No breaking changes to external APIs - functionality preserved

## Files removed/archived

- `db.py` - Database operations (moved to common)
- `cache.py` - Redis caching logic (moved to monolith)
- `app/main.py` - API endpoints (moved to monolith)
- Service-specific Docker configuration remains for reference
