"""Utility functions for the City Fog Map application."""

# H3 resolution levels are used to partition the map into a grid of hexagonal cells.
# A smaller radius requires a higher resolution (smaller hexagons) to provide more detail.
# A larger radius can use a lower resolution (larger hexagons) for better performance.
#
# The table below defines the mapping from a search radius (in meters) to the
# appropriate H3 resolution. The values have been chosen to provide a good balance
# between map detail and application performance.
#
# For reference, here are the average H3 hexagon edge lengths for the selected resolutions:
# - Resolution 13: ~4 meters
# - Resolution 12: ~11 meters
# - Resolution 11: ~29 meters
# - Resolution 10: ~76 meters
#
# Data source: https://h3geo.org/docs/core-library/restable/
RESOLUTION_MAPPING = [
    (30, 13),   # Up to 30m radius, use resolution 13
    (70, 12),   # Up to 70m radius, use resolution 12
    (150, 11),  # Up to 150m radius, use resolution 11
]
DEFAULT_RESOLUTION = 10 # For any radius larger than 150m


def radius_to_h3_resolution(radius_m: int) -> int:
    """
    Selects an appropriate H3 resolution based on a given search radius.

    The function maps a radius in meters to an H3 resolution level. A smaller
    radius results in a higher resolution (smaller hexagons) to provide more
    granular detail, while a larger radius results in a lower resolution
    (larger hexagons) to ensure better performance.

    Args:
        radius_m: The search radius in meters.

    Returns:
        The corresponding H3 resolution level (an integer between 10 and 13).
    """
    for threshold, resolution in RESOLUTION_MAPPING:
        if radius_m <= threshold:
            return resolution
    return DEFAULT_RESOLUTION
