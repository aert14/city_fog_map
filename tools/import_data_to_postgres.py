#!/usr/bin/env python3
"""
Импортирует границы районов Москвы и вычисляет их H3-покрытие для PostgreSQL.

Скрипт загружает геометрии районов и округов из GeoJSON файлов,
вычисляет покрытие H3-ячейками на заданной резолюции и сохраняет
всё в PostgreSQL с использованием PostGIS.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import h3
import psycopg2
from h3.api import basic_str as h3_basic
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

# Добавляем корневую директорию проекта в путь, чтобы найти модуль services
# Это необходимо для запуска скрипта из docker compose exec
sys.path.append('/app')

from services.common.database import get_connection
from services.common.database.connection import (
    BASE_VISIT_RESOLUTION, PRIMARY_COVERAGE_THRESHOLD
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
LOG = logging.getLogger(__name__)


def load_geojson_features(path: Path) -> List[Dict]:
    """Загружает объекты (features) из GeoJSON файла."""
    LOG.info("Загрузка объектов из %s", path)
    if not path.exists():
        LOG.error("Файл не найден: %s", path)
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    features = data.get("features", [])
    LOG.info("Загружено %d объектов из %s", len(features), path)
    return features


def clear_existing_data(conn) -> None:
    """Очищает таблицы с районами и их ячейками перед импортом."""
    LOG.warning("Очистка существующих данных в таблицах district_cells и districts...")
    with conn.cursor() as cur:
        # CASCADE удалит зависимые записи в district_cells
        cur.execute("TRUNCATE TABLE districts RESTART IDENTITY CASCADE;")
    conn.commit()
    LOG.info("Таблицы успешно очищены.")


def insert_districts(conn, features: List[Dict], level: str) -> None:
    """Вставляет записи о районах/округах в базу данных."""
    with conn.cursor() as cur:
        for feature in features:
            props = feature.get("properties", {})
            district_id = props["id"]
            name_ru = props["name_ru"]
            parent_id = props.get("parent_id")
            bbox = props.get("bbox", [])
            geom_json_str = json.dumps(feature["geometry"])

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
                geom_wkt, geom_json_str,
                bbox[0] if len(bbox) >= 1 else None,
                bbox[1] if len(bbox) >= 2 else None,
                bbox[2] if len(bbox) >= 3 else None,
                bbox[3] if len(bbox) >= 4 else None
            ))

    conn.commit()
    LOG.info("Вставлено %d записей для уровня '%s'", len(features), level)


def compute_h3_coverage(district_geom: BaseGeometry, resolution: int) -> Dict[str, float]:
    """
    Вычисляет покрытие H3-ячейками для геометрии района, используя новый H3 API.
    """
    import json

    # Convert geometry to GeoJSON format
    geojson_mapping = json.loads(json.dumps(district_geom.__geo_interface__))

    # Convert to H3 shape and get cells
    h3shape = h3_basic.geo_to_h3shape(geojson_mapping)
    hexes = h3_basic.h3shape_to_cells(h3shape, resolution)

    coverages = {}
    for h3_index in hexes:
        try:
            # Граница ячейки в формате [(lat, lng), ...]
            boundary_tuples = h3_basic.cell_to_boundary(h3_index)
            # Shapely ожидает [(lng, lat), ...]
            cell_coords = [(lng, lat) for lat, lng in boundary_tuples]
            cell_geom = Polygon(cell_coords)

            intersection = cell_geom.intersection(district_geom)
            if not intersection.is_empty:
                # Рассчитываем долю пересечения
                coverage = intersection.area / cell_geom.area
                if coverage > 1e-6: # Игнорируем очень маленькие пересечения
                    coverages[h3_index] = min(1.0, coverage)
        except Exception as e:
            LOG.warning("Не удалось обработать ячейку %s: %s", h3_index, e)

    return coverages


def compute_and_store_coverage(conn, district_id: int, geom: BaseGeometry, resolution: int) -> Tuple[int, float]:
    """Вычисляет и сохраняет H3-покрытие для района в БД."""
    LOG.info("Вычисление H3-покрытия для района ID %d...", district_id)

    coverages = compute_h3_coverage(geom, resolution)

    if not coverages:
        LOG.warning("Для района ID %d не найдено ни одной H3-ячейки.", district_id)
        return 0, 0.0

    with conn.cursor() as cur:
        # Подготовка данных для массовой вставки
        coverage_values = [
            (district_id, h3_index, coverage_val)
            for h3_index, coverage_val in coverages.items()
        ]

        psycopg2.extras.execute_values(cur, """
            INSERT INTO district_cells (district_id, h3, coverage)
            VALUES %s
            ON CONFLICT (district_id, h3) DO UPDATE SET coverage = EXCLUDED.coverage
        """, coverage_values)

        # Рассчитываем и обновляем итоговые статистики для района
        total_cells = sum(1 for coverage in coverages.values() if coverage >= PRIMARY_COVERAGE_THRESHOLD)
        total_weight = sum(coverages.values())

        cur.execute("""
            UPDATE districts
            SET total_cells = %s, total_weight = %s
            WHERE id = %s
        """, (total_cells, total_weight, district_id))

    conn.commit()
    LOG.info("Район ID %d: %d ячеек, общий вес %.2f", district_id, len(coverages), total_weight)
    return total_cells, total_weight


def main():
    """Главная функция для выполнения импорта."""
    conn = None
    try:
        conn = get_connection()
        LOG.info("Успешное подключение к базе данных.")

        # Шаг 1: Очистка старых данных
        clear_existing_data(conn)

        # Шаг 2: Загрузка и вставка округов (родительские сущности)
        okrug_path = Path("data/moscow_okrugs.geojson")
        okrug_features = load_geojson_features(okrug_path)
        if okrug_features:
            insert_districts(conn, okrug_features, "okrug")

        # Шаг 3: Загрузка и вставка районов
        district_path = Path("data/moscow_districts.geojson")
        district_features = load_geojson_features(district_path)
        if district_features:
            insert_districts(conn, district_features, "district")

            # Шаг 4: Вычисление и сохранение H3-покрытия для каждого района
            LOG.info("Начало вычисления H3-покрытия для %d районов...", len(district_features))
            for feature in district_features:
                district_id = feature["properties"]["id"]
                geom = shape(feature["geometry"])
                compute_and_store_coverage(conn, district_id, geom, BASE_VISIT_RESOLUTION)

        LOG.info("Импорт данных успешно завершен.")

    except (psycopg2.Error, ValueError) as e:
        LOG.error("Произошла ошибка: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            LOG.info("Соединение с базой данных закрыто.")


if __name__ == "__main__":
    main()