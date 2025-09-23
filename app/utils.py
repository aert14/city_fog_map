def radius_to_h3_resolution(radius_m: int) -> int:
    """Map a radius in meters to an H3 resolution."""
    if radius_m <= 30:
        return 13  # Small hexagons (~100m)
    elif radius_m <= 70:
        return 12  # Medium-small hexagons (~200m)
    elif radius_m <= 150:
        return 11  # Medium hexagons (~400m)
    else:
        return 10  # Large hexagons (~800m)
