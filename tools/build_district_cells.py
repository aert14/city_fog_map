#!/usr/bin/env python3
"""
Скрипт для импорта районов Москвы, вычисления H3-покрытия и сохранения в PostgreSQL.

Этот скрипт выполняет полный цикл подготовки геоданных:
1. Читает геометрии районов и округов из GeoJSON.
2. Вставляет их в таблицу `districts`.
3. Для каждого района вычисляет H3-покрытие на заданной резолюции.
4. Для точного расчета площади использует проекцию UTM.
5. Сохраняет данные о покрытии в таблицу `district_cells`.
6. Обновляет агрегированную статистику (`total_cells`, `total_weight`) в таблице `districts`.
7. (Опционально) Пересчитывает статистику посещений для всех пользователей.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import h3
import psycopg2
import psycopg2.extras
from h3.api import basic_str as h3_basic
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

# Добавляем корневую директорию проекта в путь для корректного импорта модулей
sys.path.append('/app')

from services.common.database import get_connection
from services.common.database.connection import (
    BASE_VISIT_RESOLUTION, PRIMARY_COVERAGE_THRESHOLD
)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
LOG = logging.getLogger(__name__)

# Используем метрическую проекцию для точного расчета площадей в районе Москвы
AREA_PROJECTION = "EPSG:32637"  # UTM zone 37N

def parse_args() -> argparse.Namespace:
    """Парсит аргументы командной строки."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recalculate-stats",
        action="store_true",
        help="Пересчитать статистику всех пользователей после обновления покрытия H3.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Уровень логирования (по умолчанию: INFO).",
    )
    return parser.parse_args()


def clear_existing_data(conn: psycopg2.extensions.connection) -> None:
    """Очищает таблицы с районами и их ячейками перед импортом."""
    LOG.warning("Очистка существующих данных в таблицах `districts` и `district_cells`...")
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE districts RESTART IDENTITY CASCADE;")
    conn.commit()
    LOG.info("Таблицы успешно очищены.")


def ensure_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    """Гарантирует, что геометрия является валидным MultiPolygon."""
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if geom.geom_type == "GeometryCollection":
        polygons = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polygons:
            return MultiPolygon()
        return MultiPolygon(polygons)
    raise TypeError(f"Неподдерживаемый тип геометрии: {geom.geom_type}")


def load_and_insert_features(conn: psycopg2.extensions.connection, path: Path, level: str) -> List[Dict]:
    """Загружает GeoJSON, вставляет в БД и возвращает список объектов."""
    LOG.info("Загрузка и вставка объектов уровня '%s' из %s", level, path)
    if not path.exists():
        LOG.error("Файл не найден: %s", path)
        return []

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    features = data.get("features", [])
    if not features:
        LOG.warning("В файле %s не найдено объектов (features).", path)
        return []

    with conn.cursor() as cur:
        for feature in features:
            props = feature.get("properties", {})
            district_id = props["id"]
            name_ru = props["name_ru"]
            parent_id = props.get("parent_id")
            bbox = props.get("bbox", [])
            
            geom = shape(feature["geometry"])

            cur.execute("""
                INSERT INTO districts (id, level, name_ru, parent_id, geom, geom_geojson,
                                     bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat)
                VALUES (%s, %s, %s, %s, ST_GeomFromWKB(%s, 4326), %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (
                district_id, level, name_ru, parent_id,
                geom.wkb, json.dumps(feature["geometry"]),
                bbox[0] if len(bbox) >= 1 else None,
                bbox[1] if len(bbox) >= 2 else None,
                bbox[2] if len(bbox) >= 3 else None,
                bbox[3] if len(bbox) >= 4 else None
            ))
    conn.commit()
    LOG.info("Вставлено %d записей для уровня '%s'", len(features), level)
    return features


def calculate_and_store_all_coverages(conn: psycopg2.extensions.connection, district_features: List[Dict], resolution: int) -> None:
    """Вычисляет и сохраняет H3-покрытие для всех районов."""
    LOG.info("Начало вычисления H3-покрытия для %d районов...", len(district_features))
    transformer = Transformer.from_crs("EPSG:4326", AREA_PROJECTION, always_xy=True)

    for feature in district_features:
        district_id = feature["properties"]["id"]
        name = feature["properties"]["name_ru"]
        LOG.debug("Обработка района: %s (ID: %d)", name, district_id)

        geom = ensure_multipolygon(shape(feature["geometry"]))
        if geom.is_empty:
            LOG.warning("Пустая геометрия для района %s (ID: %d), пропуск.", name, district_id)
            continue

        # Полифилл для получения H3-ячеек
        import json
        geojson_mapping = json.loads(json.dumps(geom.__geo_interface__))
        h3shape = h3_basic.geo_to_h3shape(geojson_mapping)
        hexes = h3_basic.h3shape_to_cells(h3shape, resolution)
        if not hexes:
            LOG.warning("Для района %s (ID: %d) не найдено H3-ячеек.", name, district_id)
            continue
            
        # Проецируем геометрию района для расчета площади
        district_proj = transform(transformer.transform, geom)

        # Вычисляем покрытие для каждой ячейки
        coverages = []
        for h3_index in hexes:
            boundary = h3_basic.cell_to_boundary(h3_index)
            cell_poly = Polygon([(lon, lat) for lat, lon in boundary])
            cell_proj = transform(transformer.transform, cell_poly)
            
            intersection_area = cell_proj.intersection(district_proj).area
            coverage = intersection_area / cell_proj.area if cell_proj.area > 0 else 0
            if coverage > 1e-6:
                coverages.append((district_id, h3_index, min(1.0, coverage)))
        
        # Сохраняем в БД
        if coverages:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur,
                    "INSERT INTO district_cells (district_id, h3, coverage) VALUES %s",
                    coverages
                )

                total_weight = sum(cov for _, _, cov in coverages)
                total_cells = sum(1 for _, _, cov in coverages if cov >= PRIMARY_COVERAGE_THRESHOLD)

                cur.execute(
                    "UPDATE districts SET total_cells = %s, total_weight = %s WHERE id = %s",
                    (total_cells, total_weight, district_id)
                )
            LOG.info("Район ID %d (%s): сохранено %d ячеек, вес %.2f, первичных ячеек %d", 
                     district_id, name, len(coverages), total_weight, total_cells)
    conn.commit()


def recalculate_user_stats(conn: psycopg2.extensions.connection) -> None:
    """Пересчитывает статистику районов и округов на основе существующих визитов."""
    LOG.info("Пересчет статистики пользователей...")

    with conn.cursor() as cur:
        LOG.warning("Очистка таблиц `user_district_stats` и `user_okrug_stats`...")
        cur.execute("TRUNCATE TABLE user_district_stats, user_okrug_stats;")

        LOG.info("Агрегация данных из `user_visits_atomic` и `district_cells`...")
        # Этот запрос одним махом собирает всю нужную информацию для обновления статистики
        cur.execute("""
            WITH visit_coverage AS (
                SELECT
                    v.user_id,
                    dc.district_id,
                    d.parent_id AS okrug_id,
                    dc.coverage,
                    CASE WHEN dc.coverage >= %s THEN 1 ELSE 0 END AS cell_credit
                FROM user_visits_atomic v
                JOIN district_cells dc ON v.h3 = dc.h3
                JOIN districts d ON dc.district_id = d.id
            ),
            district_agg AS (
                SELECT
                    user_id,
                    district_id,
                    SUM(cell_credit) AS visited_cells,
                    SUM(coverage) AS visited_weight
                FROM visit_coverage
                GROUP BY user_id, district_id
            ),
            okrug_agg AS (
                SELECT
                    user_id,
                    okrug_id,
                    SUM(cell_credit) AS visited_cells,
                    SUM(coverage) AS visited_weight
                FROM visit_coverage
                WHERE okrug_id IS NOT NULL
                GROUP BY user_id, okrug_id
            )
            INSERT INTO user_district_stats (user_id, district_id, visited_cells, visited_weight)
            SELECT user_id, district_id, visited_cells, visited_weight FROM district_agg;
        """, (PRIMARY_COVERAGE_THRESHOLD,))
        LOG.info("Таблица `user_district_stats` обновлена. (%d строк)", cur.rowcount)

        cur.execute("""
            INSERT INTO user_okrug_stats (user_id, okrug_id, visited_cells, visited_weight)
            SELECT user_id, okrug_id, visited_cells, visited_weight 
            FROM (
                SELECT
                    v.user_id,
                    d.parent_id AS okrug_id,
                    SUM(CASE WHEN dc.coverage >= %s THEN 1 ELSE 0 END) AS visited_cells,
                    SUM(dc.coverage) AS visited_weight
                FROM user_visits_atomic v
                JOIN district_cells dc ON v.h3 = dc.h3
                JOIN districts d ON dc.district_id = d.id
                WHERE d.parent_id IS NOT NULL
                GROUP BY v.user_id, d.parent_id
            ) as okrug_agg;
        """, (PRIMARY_COVERAGE_THRESHOLD,))
        LOG.info("Таблица `user_okrug_stats` обновлена. (%d строк)", cur.rowcount)
        
    conn.commit()
    LOG.info("Статистика пользователей успешно пересчитана.")


def main() -> int:
    """Главная функция для выполнения импорта."""
    args = parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))
    
    conn = None
    try:
        conn = get_connection()
        LOG.info("Успешное подключение к базе данных.")
        
        clear_existing_data(conn)
        
        # Загрузка и вставка округов
        okrug_features = load_and_insert_features(conn, Path("data/moscow_okrugs.geojson"), "okrug")
        
        # Загрузка и вставка районов
        district_features = load_and_insert_features(conn, Path("data/moscow_districts.geojson"), "district")
        
        # Вычисление и сохранение H3 покрытия
        if district_features:
            calculate_and_store_all_coverages(conn, district_features, BASE_VISIT_RESOLUTION)
        
        # Опциональный пересчет статистики
        if args.recalculate_stats:
            recalculate_user_stats(conn)
            
        LOG.info("Импорт и обработка данных успешно завершены.")
        return 0
        
    except Exception as e:
        LOG.error("Произошла критическая ошибка: %s", e, exc_info=True)
        if conn:
            conn.rollback()
        return 1
    finally:
        if conn:
            conn.close()
            LOG.info("Соединение с базой данных закрыто.")


if __name__ == "__main__":
    sys.exit(main())