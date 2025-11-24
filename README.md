# City Fog Map

Real-world "Fog of War" exploration game for Telegram. The map is hidden by fog; moving in the real world clears it.

## Functionality

- **Fog System**: Uses H3 geospatial indexing (resolution 9) to track visited areas.
- **Exploration**: Tracks user GPS location to unlock hexagons.
- **Statistics**: Calculates explored percentage for administrative districts and okrugs.
- **Leaderboards**: Ranks users by explored area (weekly/seasonal).
- **Telegram Integration**: Authenticates users via Telegram WebApp initData.
- **Demo Mode**: Frontend-only mode using local storage for testing.

## Stack

- **Backend**: Python (FastAPI), AsyncPG.
- **Database**: PostgreSQL + PostGIS.
- **Cache**: Redis (user sessions, stats caching).
- **Frontend**: Vanilla JavaScript, MapLibre GL JS.
- **Infrastructure**: Docker Compose, Nginx.

## Demo

You can try the frontend in standalone mode (no backend required):
[Launch Demo](https://aert14.github.io/city_fog_map/)

*Note: In demo mode, progress is saved to localStorage.*

## How to Run

1. Clone the repository.
2. Set `TELEGRAM_BOT_TOKEN` env var.
3. Run with Docker Compose:

```bash
make up
```

This starts the database, backend, and serves the frontend.
