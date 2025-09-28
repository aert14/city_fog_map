# User Service - Archived

This microservice has been archived as part of the monolith migration.

## What was moved to monolith

All user-related functionality has been integrated into the monolith service:
- User authentication and management
- Telegram WebApp authentication verification
- User session management
- User settings management (H3 resolution preferences)

## Original functionality

This service previously handled:
- User registration and profile management
- Telegram authentication verification
- Session-based authentication for web clients
- User settings storage and retrieval

## Migration notes

- All database operations moved to `services/common/db.py`
- Authentication logic integrated into monolith service
- Session management preserved in monolith
- No breaking changes to external APIs - functionality preserved

## Files removed/archived

- `db.py` - Database operations (moved to common)
- `app/main.py` - API endpoints (moved to monolith)
- Service-specific Docker configuration remains for reference
