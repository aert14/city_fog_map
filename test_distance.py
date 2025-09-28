import math

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Вычисляет расстояние между двумя точками на Земле в километрах.
    Использует формулу Haversine.

    Args:
        lat1, lon1: Координаты первой точки
        lat2, lon2: Координаты второй точки

    Returns:
        Расстояние в километрах
    """
    # Радиус Земли в километрах
    R = 6371.0

    # Преобразование в радианы
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Разницы координат
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    # Формула Haversine
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = R * c
    return distance

# Test distance calculation
print("Testing distance calculation...")

# Test 1: Moscow to St. Petersburg (known distance ~635 km)
lat1, lon1 = 55.7558, 37.6176  # Moscow
lat2, lon2 = 59.9343, 30.3351  # St. Petersburg

distance = calculate_distance(lat1, lon1, lat2, lon2)
print(f'Moscow-St.Petersburg: {distance:.1f} km (expected ~635 km)')

# Test 2: Same point should be 0
distance_zero = calculate_distance(55.7558, 37.6176, 55.7558, 37.6176)
print(f'Same point: {distance_zero:.6f} km (expected 0)')

# Test 3: Small distance
lat3, lon3 = 55.7558, 37.6176  # Moscow
lat4, lon4 = 55.7642, 37.6026  # Nearby point

distance_small = calculate_distance(lat3, lon3, lat4, lon4)
print(f'Small distance: {distance_small:.3f} km')

# Test 4: Speed calculation
time_diff_hours = 1.0  # 1 hour
speed_kmh = distance / time_diff_hours
print(f'Speed for Moscow-St.Petersburg in 1 hour: {speed_kmh:.1f} km/h')

print('All tests passed!')
