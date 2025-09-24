"""
Main FastAPI application for the City Fog Map.

This module sets up the FastAPI application, including logging, middleware,
static file serving, API endpoints, and lifecycle events. It serves as the
primary entry point for the backend server.
"""
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import time
from typing import List, Dict

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import h3
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from . import db as db_module
from . import utils


# --- Logging Configuration ---
# Set up basic logging to console.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Attempt to set up rotating file logging to the project root.
try:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_file_path = os.path.join(project_root, "server.log")

    # Avoid adding duplicate handlers if the module is reloaded
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', None) == log_file_path for h in logging.getLogger().handlers):
        fh = RotatingFileHandler(log_file_path, maxBytes=1_000_000, backupCount=3)
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(fh)
        logger.info(f"File logging enabled at {log_file_path}")
except Exception as e:
    logger.warning(f"Failed to set up file logging: {e}")


# --- Pydantic Models for API Requests and Responses ---

class VisitRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)


class Circle(BaseModel):
    lat: float
    lon: float
    radius_m: int = 100


class VisitResponse(BaseModel):
    added: int
    circle: Circle
    stats: Dict[str, int]


class CirclesResponse(BaseModel):
    hexagons: List[str]


class RadiusRequest(BaseModel):
    radius_m: int = Field(..., ge=1, le=1000)


class DeleteCircleRequest(BaseModel):
    geokey: str = Field(..., min_length=10, max_length=20)


class AuthRequest(BaseModel):
    initData: str


# --- FastAPI Application Setup ---

app = FastAPI(
    title="City Fog Map API",
    version="0.1.0",
    description="API for the City Fog Map Telegram Mini App.",
)

# --- Middleware Configuration ---

# Add session middleware for the debug authentication flow.
# The secret key should be set securely in a production environment.
SESSION_SECRET = os.getenv("SESSION_SECRET", os.urandom(32).hex())
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log incoming HTTP requests and their responses."""
    start_time = time.perf_counter()
    path_and_qs = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    logger.info(f"HTTP REQ {request.method} {path_and_qs} from {request.client.host}")

    response = None
    error: Exception | None = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        error = exc
        raise
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        status_code = getattr(response, "status_code", 500 if error else "n/a")
        error_info = f" error={type(error).__name__}" if error else ""
        logger.info(f"HTTP RES {request.method} {path_and_qs} -> {status_code} in {duration_ms:.1f}ms{error_info}")


# --- Static Files and Frontend Serving ---

# Mount the 'webapp' directory to serve the frontend files.
webapp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if not os.path.isdir(webapp_dir):
    os.makedirs(webapp_dir, exist_ok=True)


class LongCacheStaticFiles(StaticFiles):
    """Custom StaticFiles handler to set long cache headers for assets."""
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        # For assets like JS, CSS, images, set a long-lived, immutable cache.
        if response.status_code == 200 and "text/html" not in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

app.mount("/webapp", LongCacheStaticFiles(directory=webapp_dir, html=True), name="webapp")

# Generate a version string for cache-busting frontend assets (JS, CSS).
# This uses the modification time of app.js or the current time as a fallback.
try:
    app_js_path = os.path.join(webapp_dir, "app.js")
    APP_VERSION = str(int(os.path.getmtime(app_js_path)))
except Exception:
    APP_VERSION = str(int(time.time()))


def _read_and_inject_version_into_index() -> str:
    """Reads index.html and injects the cache-busting version query param."""
    index_path = os.path.join(webapp_dir, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        # Inject version into asset URLs
        html = html.replace("/webapp/app.js", f"/webapp/app.js?v={APP_VERSION}")
        html = html.replace("/webapp/style.css", f"/webapp/style.css?v={APP_VERSION}")
        html = html.replace("/webapp/fog.js", f"/webapp/fog.js?v={APP_VERSION}")
        return html
    except Exception as e:
        logger.error(f"Failed to read or process index.html: {e}")
        return "<html><body>Error: Application index not found.</body></html>"


# --- Application Lifecycle Events ---

@app.on_event("startup")
def on_startup():
    """Initializes the database connection and schema on application startup."""
    logger.info("Database initialization...")
    conn = db_module.get_connection()
    db_module.init_db(conn)
    logger.info("Database initialized successfully.")
    logger.info("🚀 City Fog Map API starting up...")


@app.on_event("shutdown")
async def shutdown_event():
    """Logs a message on application shutdown."""
    logger.info("🛑 City Fog Map API shutting down...")


# --- Core Endpoints ---

@app.get("/", summary="Root Redirect", include_in_schema=False)
async def root_redirect():
    """Redirects the root URL to the appropriate frontend page."""
    if auth.DEBUG_AUTH_MODE:
        return RedirectResponse(url="/webapp/debug-auth.html")
    return RedirectResponse(url="/webapp/")


@app.get("/webapp/", summary="Serve Frontend", include_in_schema=False)
async def webapp_index() -> Response:
    """Serves the main index.html file with cache-busting."""
    html = _read_and_inject_version_into_index()
    headers = {"Cache-Control": "no-store"} # Ensure index.html is not cached
    return Response(content=html, media_type="text/html; charset=utf-8", headers=headers)


@app.get("/health", summary="Health Check", tags=["System"])
async def health():
    """Provides a simple health check endpoint."""
    logger.info("Health check requested")
    return {"status": "ok"}


@app.post("/api/v1/visit", response_model=VisitResponse, summary="Record a Visit", tags=["API"])
async def visit_area(body: VisitRequest, user=Depends(auth.get_current_user)):
    """
    Records a user's visit to a specific latitude and longitude.

    This endpoint calculates the corresponding H3 geohash for the location based
    on the user's current resolution setting and saves it as an explored circle.
    """
    user_id, _ = user
    logger.info(f"Visit request: lat={body.lat}, lon={body.lon}, user_id={user_id}")
    conn = db_module.get_connection()

    user_resolution = db_module.get_user_h3_resolution(conn, user_id)
    user_radius = db_module.get_user_radius(conn, user_id)
    geokey = h3.latlng_to_cell(body.lat, body.lon, user_resolution)

    added = db_module.insert_circle_if_new(conn, user_id=user_id, geokey=geokey, lat=body.lat, lon=body.lon, radius_m=user_radius)
    total = db_module.count_circles(conn, user_id=user_id)

    logger.info(f"Visit processed: added={added}, total_circles={total}, geokey={geokey}")
    return VisitResponse(
        added=1 if added else 0,
        circle=Circle(lat=body.lat, lon=body.lon, radius_m=user_radius),
        stats={"total_circles": total},
    )


@app.get("/api/v1/circles", response_model=CirclesResponse, summary="List Explored Circles", tags=["API"])
async def list_circles(bbox: str, user=Depends(auth.get_current_user)):
    """
    Retrieves all explored circles for the user within a given bounding box.
    The `bbox` parameter should be a comma-separated string of
    `minLon,minLat,maxLon,maxLat`.
    """
    user_id, _ = user
    try:
        min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid bbox format: '{bbox}', error: {e}")
        raise HTTPException(status_code=400, detail="Invalid bbox format. Expected minLon,minLat,maxLon,maxLat.")

    conn = db_module.get_connection()
    rows = db_module.select_circles_in_bbox(conn, user_id=user_id, min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    hexagons = [r[3] for r in rows]

    logger.info(f"Circles response: {len(hexagons)} hexagons returned for user {user_id}")
    return CirclesResponse(hexagons=hexagons)


@app.post("/api/v1/radius", summary="Set Exploration Radius", tags=["API"])
async def set_radius(body: RadiusRequest, user=Depends(auth.get_current_user)):
    """
    Sets the exploration radius for the user.

    This also recalculates and stores the corresponding H3 resolution. If the
    resolution changes, all previously explored circles for the user are cleared.
    """
    user_id, _ = user
    conn = db_module.get_connection()
    radius_m = body.radius_m
    new_h3_resolution = utils.radius_to_h3_resolution(radius_m)
    old_h3_resolution = db_module.get_user_h3_resolution(conn, user_id)

    resolution_changed = old_h3_resolution != new_h3_resolution
    updated = db_module.update_radius_and_resolution_for_user(
        conn, user_id=user_id, radius_m=radius_m, h3_resolution=new_h3_resolution
    )

    if resolution_changed:
        cleared_count = db_module.clear_user_circles(conn, user_id)
        logger.info(f"Cleared {cleared_count} circles for user {user_id} due to H3 resolution change from {old_h3_resolution} to {new_h3_resolution}")

    return {"updated": updated, "h3_resolution": new_h3_resolution, "resolution_changed": resolution_changed}


# --- Debug and Development Endpoints ---

@app.get("/api/ping", summary="Ping", tags=["Debug"])
async def ping():
    """A simple endpoint to check if the API is responsive."""
    return {"ok": True}


@app.get("/api/v1/debug-mode", summary="Get Debug Status", tags=["Debug"])
async def debug_mode():
    """Returns the current debug mode status for the frontend."""
    return {
        "debug_auth_mode": auth.DEBUG_AUTH_MODE,
        "no_auth_mode": auth.NO_AUTH_MODE
    }


@app.post("/api/auth", summary="Debug Authentication", tags=["Debug"])
async def debug_auth(body: AuthRequest, request: Request):
    """
    A debug endpoint to authenticate a user via a session cookie.
    This takes a raw `initData` string, verifies it, and sets a session
    cookie for subsequent requests. Only intended for use with debug-auth.html.
    """
    logger.info(f"/api/auth called; initData length={len(body.initData)}")
    result = auth.verify_init_data(body.initData, auth.TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        logger.warning(f"/api/auth unauthorized: {result.get('reason')}")
        raise HTTPException(status_code=403, detail="unauthorized")

    payload = result["payload"]
    user_raw = payload.get("user")
    user_obj = json.loads(user_raw) if user_raw else {}

    request.session["tg_authenticated"] = True
    request.session["tg_user_id"] = user_obj.get("id")
    request.session["tg_user"] = user_obj
    logger.info(f"Debug session set for user_id {user_obj.get('id')}")
    return {"ok": True}


@app.get("/api/me", summary="Get Session User", tags=["Debug"])
async def debug_me(request: Request):
    """Returns the user object from the current session, for debugging."""
    if not request.session.get("tg_authenticated"):
        raise HTTPException(status_code=403, detail="unauthorized")
    return {"ok": True, "user": request.session.get("tg_user")}


@app.delete("/api/v1/circle", summary="Delete a Circle", tags=["Debug"])
async def delete_circle(body: DeleteCircleRequest, user=Depends(auth.get_current_user)):
    """Deletes a specific explored circle for the user (for debugging)."""
    user_id, _ = user
    conn = db_module.get_connection()
    deleted = db_module.delete_circle_by_geokey(conn, user_id=user_id, geokey=body.geokey)
    logger.info(f"Deleted {deleted} circle(s) with geokey {body.geokey} for user {user_id}")
    return {"deleted": int(deleted)}


@app.post("/api/v1/dev/clear-db", summary="Clear Database", tags=["Debug"])
async def dev_clear_db():
    """
    (Dev only) Clears all user and circle data from the database.
    This endpoint is only active when `DEBUG_AUTH_MODE` or `NO_AUTH_MODE` is enabled.
    """
    if not (auth.DEBUG_AUTH_MODE or auth.NO_AUTH_MODE):
        raise HTTPException(status_code=403, detail="forbidden")
    conn = db_module.get_connection()
    cleared_circles, cleared_users = db_module.clear_all(conn)
    logger.warning(f"DEV clear-db executed: circles={cleared_circles}, users={cleared_users}")
    return {"cleared_circles": cleared_circles, "cleared_users": cleared_users}
