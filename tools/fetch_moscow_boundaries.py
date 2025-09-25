#!/usr/bin/env python3
"""Fetch Moscow administrative boundaries (okrugs and districts) from Overpass.

This script downloads polygons for Moscow administrative okrugs and districts,
simplifies their geometries for frontend usage, and stores them as GeoJSON
files. It also records helper attributes such as bounding boxes and parent
relationships (district → okrug).

Usage example:

    python tools/fetch_moscow_boundaries.py \
        --output-dir data \
        --simplify-tolerance-m 10

The script relies on the public Overpass API and respects the recommended
request rate limits. For bulk usage, consider running against a local Overpass
instance.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from osm2geojson import json2geojson
from shapely import make_valid, prepared
from shapely.geometry import MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.validation import make_valid as make_valid_alias  # type: ignore[attr-defined]

try:  # Shapely < 2.0 compatibility
    MAKE_VALID = make_valid_alias  # type: ignore[assignment]
except Exception:  # pragma: no cover - fallback for Shapely >= 2.0
    MAKE_VALID = make_valid

try:
    from pyproj import CRS, Transformer
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "pyproj is required. Install dependencies via `pip install -r requirements.txt`."
    ) from exc


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_TOLERANCE_M = 10.0
MOSCOW_OUT_OKRUGS = "moscow_okrugs.geojson"
MOSCOW_OUT_DISTRICTS = "moscow_districts.geojson"
MOSCOW_RELATION_ID = 102269


MOSCOW_RELATION_QUERY = f"""
[out:json][timeout:180];
rel({MOSCOW_RELATION_ID});
out geom;
"""


OKRUGS_QUERY_TEMPLATE = """
[out:json][timeout:180];
area["boundary"="administrative"]["name:ru"="Москва"]["admin_level"="4"]->.moscow;
rel(area.moscow)["boundary"="administrative"]["admin_level"~"^(5|6)$"]["name:ru"~"(округ|АО)",i];
out geom;
"""


DISTRICTS_QUERY_TEMPLATE = """
[out:json][timeout:240];
area["boundary"="administrative"]["name:ru"="Москва"]["admin_level"="4"]->.moscow;
rel(area.moscow)["boundary"="administrative"]["admin_level"~"^(8|9)$"]["name:ru"~"(район|поселение)",i];
out geom;
"""


LOG = logging.getLogger("fetch_moscow_boundaries")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="data",
        type=Path,
        help="Directory where GeoJSON files will be written.",
    )
    parser.add_argument(
        "--simplify-tolerance-m",
        type=float,
        default=DEFAULT_TOLERANCE_M,
        help="Simplification tolerance in meters (Web Mercator).",
    )
    parser.add_argument(
        "--overpass-url",
        default=OVERPASS_URL,
        help="Custom Overpass API endpoint.",
    )
    parser.add_argument(
        "--sleep-after-request",
        type=float,
        default=2.0,
        help="Seconds to sleep after each Overpass request to be polite.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


def fetch_overpass(url: str, query: str) -> Dict:
    LOG.info("Overpass request: %.60s...", " ".join(query.split()))
    resp = requests.post(url, data={"data": query}, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    if "elements" not in data:
        raise RuntimeError("Unexpected Overpass response format")
    return data


def to_geojson_features(osm_json: Dict) -> List[Dict]:
    # Use osm2geojson but preserve tags for name extraction
    geojson = json2geojson(osm_json)
    features: List[Dict] = []
    for original, converted in zip(osm_json.get("elements", []), geojson.get("features", [])):
        tags = original.get("tags", {})
        converted.setdefault("properties", {}).setdefault("_raw_tags", tags)
        features.append(converted)
    LOG.debug("Converted to %d GeoJSON features", len(features))
    return features


def init_transformers() -> Tuple[Transformer, Transformer]:
    wgs84 = CRS.from_epsg(4326)
    web_mercator = CRS.from_epsg(3857)
    forward = Transformer.from_crs(wgs84, web_mercator, always_xy=True)
    backward = Transformer.from_crs(web_mercator, wgs84, always_xy=True)
    return forward, backward


def simplify_geometry(geom: BaseGeometry, tolerance_m: float, transformers: Tuple[Transformer, Transformer]) -> BaseGeometry:
    if tolerance_m <= 0:
        return geom
    forward, backward = transformers
    projected = transform(forward.transform, geom)
    simplified = projected.simplify(tolerance_m, preserve_topology=True)
    return transform(backward.transform, simplified)


def ensure_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    from shapely.geometry import MultiPolygon as MP, Polygon

    geom = MAKE_VALID(geom)
    if geom.is_empty:
        return MP()
    if isinstance(geom, MP):
        return geom
    if isinstance(geom, Polygon):  # type: ignore[name-defined]
        return MP([geom])
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in {"Polygon", "MultiPolygon"}]
        if not polys:
            return MP()
        union = polys[0]
        for other in polys[1:]:
            union = union.union(other)
        return ensure_multipolygon(union)
    raise ValueError(f"Unsupported geometry type: {geom.geom_type}")


def extract_name(feature: Dict) -> str:
    props = feature.get("properties", {})
    tags = props.get("_raw_tags", {})
    for key in ("name:ru", "name", "official_name:ru", "official_name"):
        if props.get(key):
            return str(props[key])
        if tags.get(key):
            return str(tags[key])
    raise ValueError("Feature without a usable name")


def clip_to_moscow(geom: BaseGeometry, moscow_geom: BaseGeometry) -> BaseGeometry:
    if moscow_geom.is_empty:
        return geom
    if geom.within(moscow_geom):
        return geom
    clipped = geom.intersection(moscow_geom)
    return clipped if not clipped.is_empty else geom


def build_feature_entry(
    osm_id: int,
    level: str,
    name_ru: str,
    geom: MultiPolygon,
    parent_id: Optional[int],
) -> Dict:
    minx, miny, maxx, maxy = geom.bounds
    return {
        "type": "Feature",
        "properties": {
            "id": osm_id,
            "level": level,
            "name_ru": name_ru,
            "parent_id": parent_id,
            "bbox": [minx, miny, maxx, maxy],
        },
        "geometry": json.loads(json.dumps(geom.__geo_interface__)),
    }


def assemble_boundary_catalog(
    okrug_features: List[Dict],
    district_features: List[Dict],
    moscow_geom: BaseGeometry,
    tolerance_m: float,
) -> Tuple[List[Dict], List[Dict]]:
    transformers = init_transformers()

    okrugs: List[Dict] = []
    okrug_geoms: Dict[int, BaseGeometry] = {}
    for feature in okrug_features:
        osm_id = int(feature["properties"]["id"])
        geom = ensure_multipolygon(shape(feature["geometry"]))
        geom = clip_to_moscow(geom, moscow_geom)
        geom = simplify_geometry(geom, tolerance_m, transformers)
        geom = ensure_multipolygon(geom)
        name_ru = extract_name(feature)
        okrugs.append(build_feature_entry(osm_id, "okrug", name_ru, geom, None))
        okrug_geoms[osm_id] = prepared.prep(geom)

    districts: List[Dict] = []
    for feature in district_features:
        osm_id = int(feature["properties"]["id"])
        geom = ensure_multipolygon(shape(feature["geometry"]))
        geom = clip_to_moscow(geom, moscow_geom)
        geom = simplify_geometry(geom, tolerance_m, transformers)
        geom = ensure_multipolygon(geom)
        name_ru = extract_name(feature)

        centroid = geom.representative_point()
        parent_id: Optional[int] = None
        for okrug_id, prepared_geom in okrug_geoms.items():
            if prepared_geom.contains(centroid):
                parent_id = okrug_id
                break

        districts.append(build_feature_entry(osm_id, "district", name_ru, geom, parent_id))

    return okrugs, districts


def combine_moscow_geometry(features: List[Dict]) -> BaseGeometry:
    if not features:
        raise RuntimeError("Failed to fetch Moscow boundary")
    geom = ensure_multipolygon(shape(features[0]["geometry"]))
    return geom


def dump_geojson(path: Path, features: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Wrote %s (%d features)", path, len(features))


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    LOG.info("Fetching Moscow boundary...")
    moscow_raw = fetch_overpass(args.overpass_url, MOSCOW_RELATION_QUERY)
    moscow_features = to_geojson_features(moscow_raw)
    moscow_geom = combine_moscow_geometry(moscow_features)

    LOG.info("Fetching okrugs...")
    okrugs_raw = fetch_overpass(args.overpass_url, OKRUGS_QUERY_TEMPLATE)
    time.sleep(args.sleep_after_request)
    okrug_features = to_geojson_features(okrugs_raw)

    LOG.info("Fetching districts...")
    districts_raw = fetch_overpass(args.overpass_url, DISTRICTS_QUERY_TEMPLATE)
    time.sleep(args.sleep_after_request)
    district_features = to_geojson_features(districts_raw)

    LOG.info(
        "Fetched %d okrug candidates and %d district candidates",
        len(okrug_features),
        len(district_features),
    )

    okrugs, districts = assemble_boundary_catalog(
        okrug_features, district_features, moscow_geom, args.simplify_tolerance_m
    )

    okrug_path = args.output_dir / MOSCOW_OUT_OKRUGS
    district_path = args.output_dir / MOSCOW_OUT_DISTRICTS

    dump_geojson(okrug_path, okrugs)
    dump_geojson(district_path, districts)

    LOG.info("Completed. Okrugs: %d, districts: %d", len(okrugs), len(districts))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:  # pragma: no cover - user interruption
        LOG.warning("Interrupted by user")
        sys.exit(130)

