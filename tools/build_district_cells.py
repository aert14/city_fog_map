#!/usr/bin/env python3
"""Compute H3 coverage for Moscow districts and store in SQLite.

This utility reads district geometries from GeoJSON, performs an H3 polyfill
at the configured base resolution, estimates coverage of every hexagon within
each district, and persists the results into the `district_cells` table. The
script also updates aggregate statistics (`total_cells`, `total_weight`) in the
`districts` table.

Usage example:

    python tools/build_district_cells.py \
        --geojson /path/to/data/moscow_districts.geojson \
        --database /path/to/db.sqlite3

The script requires the `h3`, `shapely`, and `pyproj` packages.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple

from h3.api import basic_str as h3_basic
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


LOG = logging.getLogger("build_district_cells")

BASE_RESOLUTION = 10
MIN_PRIMARY_COVERAGE = 0.5
AREA_PROJECTION = "EPSG:32637"  # UTM zone 37N, covering Moscow region


try:
    MAKE_VALID = make_valid
except Exception:  # pragma: no cover - fallback if shapely.make_valid missing
    from shapely.validation import make_valid as MAKE_VALID  # type: ignore


@dataclass
class DistrictFeature:
    district_id: int
    name: str
    geometry: MultiPolygon


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--geojson",
        type=Path,
        default=Path("data/moscow_districts.geojson"),
        help="Path to the districts GeoJSON (default: data/moscow_districts.geojson).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("db.sqlite3"),
        help="Path to the SQLite database file (default: db.sqlite3).",
    )
    parser.add_argument(
        "--resolution",
        type=int,
        default=BASE_RESOLUTION,
        help="H3 resolution to use for polyfill (default: 10).",
    )
    parser.add_argument(
        "--min-primary-coverage",
        type=float,
        default=MIN_PRIMARY_COVERAGE,
        help="Threshold for counting a hex as a primary cell (default: 0.5).",
    )
    parser.add_argument(
        "--recalculate-stats",
        action="store_true",
        help="Recalculate user stats after changing H3 resolution.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def ensure_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    geom = MAKE_VALID(geom)
    if geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if geom.geom_type == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in {"Polygon", "MultiPolygon"}]
        if not parts:
            return MultiPolygon()
        union = parts[0]
        for other in parts[1:]:
            union = union.union(other)
        return ensure_multipolygon(union)
    raise ValueError(f"Unsupported geometry type: {geom.geom_type}")


def load_district_features(path: Path) -> List[DistrictFeature]:
    LOG.info("Loading districts from %s", path)
    data = json.loads(path.read_text(encoding="utf-8"))
    features: List[DistrictFeature] = []
    for feature in data.get("features", []):
        props: Dict = feature.get("properties", {})
        if props.get("level") != "district":
            continue
        district_id = int(props["id"])
        name = str(props.get("name_ru") or props.get("name") or district_id)
        geom = ensure_multipolygon(shape(feature["geometry"]))
        features.append(DistrictFeature(district_id, name, geom))
    if not features:
        raise RuntimeError("No district features found in GeoJSON")
    LOG.info("Loaded %d district features", len(features))
    return features


def make_projector() -> Transformer:
    return Transformer.from_crs("EPSG:4326", AREA_PROJECTION, always_xy=True)


def project_geometry(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    return transform(transformer.transform, geom)


def cell_polygon(h3_index: str) -> Polygon:
    boundary_latlon = h3_basic.cell_to_boundary(h3_index)
    # H3 returns (lat, lon); shapely expects (lon, lat)
    coords = [(lon, lat) for lat, lon in boundary_latlon]
    if not coords:
        raise ValueError(f"Empty boundary for cell {h3_index}")
    # Ensure closure
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return Polygon(coords)


def iter_cell_coverages(
    district_proj: BaseGeometry,
    h3_indexes: Iterable[str],
    transformer: Transformer,
) -> Iterator[Tuple[str, float]]:
    for h3_index in h3_indexes:
        cell_geom = cell_polygon(h3_index)
        cell_proj = project_geometry(cell_geom, transformer)
        cell_area = cell_proj.area
        if cell_area <= 0:
            LOG.debug("Skipping zero-area cell %s", h3_index)
            continue
        inter_area = cell_proj.intersection(district_proj).area
        if inter_area <= 0:
            continue
        coverage = max(0.0, min(1.0, inter_area / cell_area))
        yield h3_index, coverage


def compute_coverages(
    districts: List[DistrictFeature],
    resolution: int,
) -> Tuple[Dict[int, Dict[str, float]], Dict[int, str]]:
    transformer = make_projector()
    coverages: Dict[int, Dict[str, float]] = {}
    district_names: Dict[int, str] = {}

    for feature in districts:
        LOG.debug("Polyfilling district %s (%d)", feature.name, feature.district_id)
        geojson_mapping = json.loads(json.dumps(feature.geometry.__geo_interface__))
        h3shape = h3_basic.geo_to_h3shape(geojson_mapping)
        hexes = h3_basic.h3shape_to_cells(h3shape, resolution)
        if not hexes:
            LOG.warning("District %s (%d) produced no hexes", feature.name, feature.district_id)
            coverages[feature.district_id] = {}
            continue

        district_proj = project_geometry(feature.geometry, transformer)
        district_coverages: Dict[str, float] = {}
        for h3_index, coverage in iter_cell_coverages(district_proj, hexes, transformer):
            # Guarantee single row per district/cell as required
            district_coverages[h3_index] = coverage
        coverages[feature.district_id] = district_coverages
        district_names[feature.district_id] = feature.name
        LOG.debug(
            "District %s (%d): %d cells (sum coverage %.2f)",
            feature.name,
            feature.district_id,
            len(district_coverages),
            sum(district_coverages.values()),
        )

    return coverages, district_names


def persist_coverages(
    conn: sqlite3.Connection,
    coverages: Dict[int, Dict[str, float]],
    district_names: Dict[int, str],
    min_primary_coverage: float,
) -> None:
    LOG.info("Clearing existing district_cells data")
    conn.execute("DELETE FROM district_cells")

    insert_values: List[Tuple[int, str, float]] = []
    aggregates: Dict[int, Tuple[int, float]] = {}

    for district_id, cells in coverages.items():
        if not cells:
            aggregates[district_id] = (0, 0.0)
            continue
        total_cells = 0
        total_weight = 0.0
        for h3_index, coverage in cells.items():
            insert_values.append((district_id, h3_index, coverage))
            if coverage >= min_primary_coverage:
                total_cells += 1
            total_weight += coverage
        aggregates[district_id] = (total_cells, total_weight)

    LOG.info("Inserting %d district_cells rows", len(insert_values))
    conn.executemany(
        "INSERT INTO district_cells (district_id, h3, coverage) VALUES (?, ?, ?)",
        insert_values,
    )

    LOG.info("Updating district aggregates")
    for district_id, (total_cells, total_weight) in aggregates.items():
        conn.execute(
            "UPDATE districts SET total_cells = ?, total_weight = ? WHERE id = ?",
            (total_cells, total_weight, district_id),
        )

    LOG.info("Validating coverage plausibility")
    for district_id, (total_cells, total_weight) in aggregates.items():
        if total_cells > 0 and total_weight <= total_cells * 0.6:
            name = district_names.get(district_id, str(district_id))
            LOG.warning(
                "Suspicious coverage for %s (%d): total_weight %.3f <= 0.6 * total_cells %d",
                name,
                district_id,
                total_weight,
                total_cells,
            )

    conn.commit()


def recalculate_user_stats(conn: sqlite3.Connection) -> None:
    """
    Recalculate user district and okrug stats based on existing visits.
    This is needed when H3 resolution changes.
    """
    print("Recalculating user stats from visits...")

    # Clear existing stats
    conn.execute("DELETE FROM user_district_stats")
    conn.execute("DELETE FROM user_okrug_stats")
    conn.commit()

    # Get all visits
    cur = conn.execute("""
        SELECT user_id, h3
        FROM user_visits_atomic
        ORDER BY user_id
    """)
    visits = cur.fetchall()

    print(f"Processing {len(visits)} visits...")

    for user_id, h3_index in visits:
        # Find district for this H3 cell
        district_row = conn.execute("""
            SELECT district_id, coverage FROM district_cells
            WHERE h3 = ?
        """, (h3_index,)).fetchone()

        if not district_row:
            continue

        district_id, cell_coverage = district_row
        okrug_id = conn.execute("""
            SELECT parent_id FROM districts WHERE id = ?
        """, (district_id,)).fetchone()

        if okrug_id:
            okrug_id = okrug_id[0]
        else:
            okrug_id = None

        # Update district stats
        increment_cell = 1 if cell_coverage >= 0.5 else 0

        conn.execute("""
            INSERT INTO user_district_stats (user_id, district_id, visited_cells, visited_weight)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, district_id) DO UPDATE SET
                visited_cells = visited_cells + ?,
                visited_weight = visited_weight + ?
        """, (user_id, district_id, increment_cell, cell_coverage, increment_cell, cell_coverage))

        # Update okrug stats if applicable
        if okrug_id is not None:
            conn.execute("""
                INSERT INTO user_okrug_stats (user_id, okrug_id, visited_cells, visited_weight)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, okrug_id) DO UPDATE SET
                    visited_cells = visited_cells + ?,
                    visited_weight = visited_weight + ?
            """, (user_id, okrug_id, increment_cell, cell_coverage, increment_cell, cell_coverage))

    conn.commit()
    print("User stats recalculated.")


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    features = load_district_features(args.geojson)
    coverages, district_names = compute_coverages(features, args.resolution)

    conn = sqlite3.connect(args.database)
    try:
        persist_coverages(conn, coverages, district_names, args.min_primary_coverage)
        if args.recalculate_stats:
            recalculate_user_stats(conn)
    finally:
        conn.close()

    LOG.info("Done. Processed %d districts", len(coverages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


