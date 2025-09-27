# Visit Service - Archived

This microservice has been archived as part of the monolith migration.

## What was moved to monolith

All visit-related functionality has been integrated into the monolith service:
- Visit recording and statistics
- User visit tracking
- District and okrug statistics updates
- H3 geokey calculations

## Original functionality

This service previously handled:
- POST /api/v1/visit - Record user visits with location data
- Integration with RabbitMQ for async processing
- Database operations for visits, circles, and statistics

## Migration notes

- All database operations moved to `services/common/db.py`
- Visit endpoints now available through monolith service
- No breaking changes to external APIs - functionality preserved

## Files removed/archived

- `db.py` - Database operations (moved to common)
- `app/main.py` - API endpoints (moved to monolith)
- Service-specific Docker configuration remains for reference
