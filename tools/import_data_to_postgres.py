#!/usr/bin/env python3
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

import h3
import psycopg2
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

LOG = logging.getLogger("import_data_to_postgres")

BASE_RESOLUTION = 10
MIN_PRIMARY_COVERAGE = 0.5


def get_connection():
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL)


def load_geojson_features(path: Path) -> List[Dict]:
    LOG.info("Loading features from %s", path)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    features = data.get("features", [])
    LOG.info("Loaded %d features from %s", len(features), path)
    return features


def insert_districts(conn, features: List[Dict], level: str) -> None:
    with conn.cursor() as cur:
        for feature in features:
            props = feature.get("properties", {})
            district_id = props["id"]
            name_ru = props["name_ru"]
            parent_id = props.get("parent_id")
            bbox = props.get("bbox", [])

            geom = shape(feature["geometry"])
            geom_wkt = geom.wkt

            cur.execute("""
                INSERT INTO districts (id, level, name_ru, parent_id, geom, geom_geojson,
                                     bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat)
                VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    level = EXCLUDED.level,
                    name_ru = EXCLUDED.name_ru,
                    parent_id = EXCLUDED.parent_id,
                    geom = EXCLUDED.geom,
                    geom_geojson = EXCLUDED.geom_geojson,
                    bbox_min_lon = EXCLUDED.bbox_min_lon,
                    bbox_min_lat = EXCLUDED.bbox_min_lat,
                    bbox_max_lon = EXCLUDED.bbox_max_lon,
                    bbox_max_lat = EXCLUDED.bbox_max_lat
            """, (
                district_id, level, name_ru, parent_id,
                geom_wkt, json.dumps(feature["geometry"]),
                bbox[0] if len(bbox) >= 1 else None,
                bbox[1] if len(bbox) >= 2 else None,
                bbox[2] if len(bbox) >= 3 else None,
                bbox[3] if len(bbox) >= 4 else None
            ))

    conn.commit()
    LOG.info("Inserted %d %s records", len(features), level)


def compute_h3_coverage(district_geom: BaseGeometry, resolution: int) -> Dict[str, float]:
    geojson_geom = json.loads(json.dumps(district_geom.__geo_interface__))

    h3shape = h3.geo_to_h3shape(geojson_geom)
    hexes = h3.h3shape_to_cells(h3shape, resolution)

    coverages = {}
    for h3_index in hexes:
        boundary = h3.cell_to_boundary(h3_index)

        coords = [(lng, lat) for lat, lng in boundary]
        coords.append(coords[0])

        from shapely.geometry import Polygon
        cell_geom = Polygon(coords)

        intersection = cell_geom.intersection(district_geom)
        if not intersection.is_empty:
            coverage = intersection.area / cell_geom.area
            if coverage > 0:
                coverages[h3_index] = min(1.0, coverage)

    return coverages


def compute_and_store_coverage(conn, district_id: int, geom: BaseGeometry, resolution: int) -> Tuple[int, float]:
    LOG.info("Computing H3 coverage for district %d", district_id)

    coverages = compute_h3_coverage(geom, resolution)

    with conn.cursor() as cur:
        coverage_values = [(district_id, h3_index, coverage) for h3_index, coverage in coverages.items()]

        if coverage_values:
            cur.executemany("""
                INSERT INTO district_cells (district_id, h3, coverage)
                VALUES (%s, %s, %s)
                ON CONFLICT (district_id, h3) DO UPDATE SET coverage = EXCLUDED.coverage
            """, coverage_values)

        total_cells = sum(1 for coverage in coverages.values() if coverage >= MIN_PRIMARY_COVERAGE)
        total_weight = sum(coverages.values())

        cur.execute("""
            UPDATE districts
            SET total_cells = %s, total_weight = %s
            WHERE id = %s
        """, (total_cells, total_weight, district_id))

    conn.commit()
    LOG.info("District %d: %d cells, total weight %.2f", district_id, len(coverages), total_weight)
    return total_cells, total_weight


def main():
    logging.basicConfig(level=logging.INFO)
    conn = get_connection()

    try:
        import sys
        sys.path.append('/app')
        from app.db import init_db
        init_db(conn)

        okrug_path = Path("data/moscow_okrugs.geojson")
        if okrug_path.exists():
            okrug_features = load_geojson_features(okrug_path)
            insert_districts(conn, okrug_features, "okrug")
        else:
            LOG.warning("Okrug data file not found: %s", okrug_path)

        district_path = Path("data/moscow_districts.geojson")
        if district_path.exists():
            district_features = load_geojson_features(district_path)
            insert_districts(conn, district_features, "district")

            for feature in district_features:
                district_id = feature["properties"]["id"]
                geom = shape(feature["geometry"])
                compute_and_store_coverage(conn, district_id, geom, BASE_RESOLUTION)
        else:
            LOG.warning("District data file not found: %s", district_path)

        LOG.info("Data import completed successfully")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
