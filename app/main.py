import os
import json
import hmac
import hashlib
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
import h3

from . import db as db_module


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    # Do not crash on import; raise on first guarded route.
    pass


def verify_init_data(raw_init_data: str, bot_token: str, max_age_sec: int = 86400) -> Dict:
    data = dict(urllib.parse.parse_qsl(raw_init_data, keep_blank_values=True))
    recv_hash = data.pop("hash", None)
    if not recv_hash:
        return {"ok": False, "reason": "missing hash"}

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    exp_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(exp_hash, recv_hash):
        return {"ok": False, "reason": "hash mismatch"}

    try:
        auth_ts = int(data.get("auth_date", "0"))
    except ValueError:
        return {"ok": False, "reason": "bad auth_date"}
    if auth_ts <= 0:
        return {"ok": False, "reason": "empty auth_date"}
    if datetime.now(timezone.utc) - datetime.fromtimestamp(auth_ts, tz=timezone.utc) > timedelta(seconds=max_age_sec):
        return {"ok": False, "reason": "stale auth_date"}

    return {"ok": True, "payload": data}


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
    circles: List[Circle]


async def get_current_user(telegram_init: Optional[str] = Header(default=None, alias="X-Telegram-Init")) -> Tuple[int, Optional[str]]:
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Server misconfigured: TELEGRAM_BOT_TOKEN not set")
    if not telegram_init:
        raise HTTPException(status_code=401, detail="missing initData")

    result = verify_init_data(telegram_init, TELEGRAM_BOT_TOKEN)
    if not result.get("ok"):
        raise HTTPException(status_code=401, detail=result.get("reason", "bad initData"))

    payload = result["payload"]
    user_raw = payload.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="no user in initData")
    try:
        user_obj = json.loads(user_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="bad user json")

    tg_id = int(user_obj.get("id"))
    username = user_obj.get("username")

    # Ensure user exists in DB, return internal user id
    conn = db_module.get_connection()
    user_id = db_module.ensure_user(conn, tg_id=tg_id, username=username)
    print(f"verify_init_data: OK user_id={user_id} tg_id={tg_id}")
    return user_id, username


app = FastAPI(title="City Fog Map API", version="0.1.0")

# Static frontend at /webapp
webapp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
if not os.path.isdir(webapp_dir):
    os.makedirs(webapp_dir, exist_ok=True)
app.mount("/webapp", StaticFiles(directory=webapp_dir, html=True), name="webapp")


@app.on_event("startup")
def on_startup() -> None:
    conn = db_module.get_connection()
    db_module.init_db(conn)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/visit", response_model=VisitResponse)
async def visit_area(body: VisitRequest, user=Depends(get_current_user)):
    user_id, _ = user
    conn = db_module.get_connection()

    lat, lon = float(body.lat), float(body.lon)
    # H3 resolution approximately ~100m per hexagon
    resolution = 11
    geokey = h3.geo_to_h3(lat, lon, resolution)

    added = db_module.insert_circle_if_new(conn, user_id=user_id, geokey=geokey, lat=lat, lon=lon)
    total = db_module.count_circles(conn, user_id=user_id)

    return VisitResponse(
        added=1 if added else 0,
        circle=Circle(lat=lat, lon=lon, radius_m=100),
        stats={"total_circles": total},
    )


@app.get("/api/v1/circles", response_model=CirclesResponse)
async def list_circles(bbox: str, user=Depends(get_current_user)):
    user_id, _ = user
    try:
        min_lon_str, min_lat_str, max_lon_str, max_lat_str = bbox.split(",")
        min_lon, min_lat = float(min_lon_str), float(min_lat_str)
        max_lon, max_lat = float(max_lon_str), float(max_lat_str)
    except Exception:
        raise HTTPException(status_code=400, detail="bad bbox")

    conn = db_module.get_connection()
    rows = db_module.select_circles_in_bbox(conn, user_id=user_id, min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    items = [Circle(lat=r[0], lon=r[1], radius_m=int(r[2])) for r in rows]
    return CirclesResponse(circles=items)


