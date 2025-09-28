import time
import math

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points using Haversine formula"""
    R = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = R * c
    return distance

def should_reject_visit(last_lat, last_lon, last_timestamp, current_lat, current_lon, current_timestamp):
    """Simulate the speed check logic"""
    time_diff_seconds = current_timestamp - last_timestamp

    if time_diff_seconds <= 0:
        return False, "invalid_time"

    distance_km = calculate_distance(last_lat, last_lon, current_lat, current_lon)
    speed_kmh = (distance_km / time_diff_seconds) * 3600

    print(f"Speed check: distance={distance_km:.3f}km, time_diff={time_diff_seconds:.1f}s, speed={speed_kmh:.1f}km/h")
    # Check conditions: teleport first, then excessive speed
    if distance_km > 2.0 and time_diff_seconds < 10.0:
        return True, f"teleport_{distance_km:.3f}km_in_{time_diff_seconds:.1f}s"

    if speed_kmh > 150.0:
        return True, f"excessive_speed_{speed_kmh:.1f}"

    return False, "ok"

print("Testing speed validation logic...")

# Test 1: Normal visit (should pass)
last_lat, last_lon = 55.7558, 37.6176  # Moscow
last_timestamp = time.time() - 60  # 1 minute ago
current_lat, current_lon = 55.7642, 37.6026  # ~1.3km away
current_timestamp = time.time()

reject, reason = should_reject_visit(last_lat, last_lon, last_timestamp, current_lat, current_lon, current_timestamp)
print(f"Test 1 (normal): reject={reject}, reason={reason}")
assert not reject, f"Normal visit should pass, but got {reason}"

# Test 2: Excessive speed (should reject)
# Distance ~633km in 1 hour = 633 km/h > 150 km/h
last_lat, last_lon = 55.7558, 37.6176  # Moscow
last_timestamp = time.time() - 3600  # 1 hour ago
current_lat, current_lon = 59.9343, 30.3351  # St. Petersburg
current_timestamp = time.time()

reject, reason = should_reject_visit(last_lat, last_lon, last_timestamp, current_lat, current_lon, current_timestamp)
print(f"Test 2 (excessive speed): reject={reject}, reason={reason}")
assert reject, f"Excessive speed should be rejected, but got {reason}"
assert "excessive_speed" in reason, f"Should be excessive_speed, got {reason}"

# Test 3: Teleport (should reject)
# Distance > 2km in < 10 seconds, but speed < 150 km/h
last_lat, last_lon = 55.7558, 37.6176  # Moscow
last_timestamp = time.time() - 8  # 8 seconds ago
current_lat, current_lon = 55.7558 + 0.02, 37.6176  # ~2.2km away (rough approximation)
current_timestamp = time.time()

reject, reason = should_reject_visit(last_lat, last_lon, last_timestamp, current_lat, current_lon, current_timestamp)
print(f"Test 3 (teleport): reject={reject}, reason={reason}")
assert reject, f"Teleport should be rejected, but got {reason}"
assert "teleport" in reason, f"Should be teleport, got {reason}"

# Test 4: Fast but not excessive speed (should pass)
# Distance ~100km in 1 hour = 100 km/h < 150 km/h
last_lat, last_lon = 55.7558, 37.6176  # Moscow
last_timestamp = time.time() - 3600  # 1 hour ago
current_lat, current_lon = 55.7558 + 0.9, 37.6176  # ~100km away (rough approximation)
current_timestamp = time.time()

reject, reason = should_reject_visit(last_lat, last_lon, last_timestamp, current_lat, current_lon, current_timestamp)
print(f"Test 4 (fast but ok): reject={reject}, reason={reason}")
assert not reject, f"Fast but acceptable speed should pass, but got {reason}"

print("All speed validation tests passed!")
