"""Small, dependency-free geometry helpers using normalized image coordinates."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Return True when a point is inside a polygon (boundary counts as inside)."""
    if len(polygon) < 3:
        raise ValueError("A polygon needs at least three points")

    px, py = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if point_to_segment_distance(point, previous, current) <= 1e-9:
            return True
        x1, y1 = previous
        x2, y2 = current
        crosses_ray = (y1 > py) != (y2 > py)
        if crosses_ray:
            crossing_x = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < crossing_x:
                inside = not inside
        previous = current
    return inside


def point_to_segment_distance(point: Point, start: Point, end: Point) -> float:
    """Euclidean distance between a point and a finite line segment."""
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(px - x1, py - y1)
    projection = ((px - x1) * dx + (py - y1) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    nearest = (x1 + projection * dx, y1 + projection * dy)
    return math.hypot(px - nearest[0], py - nearest[1])


def signed_distance_to_polygon(point: Point, polygon: Sequence[Point]) -> float:
    """Distance to the nearest edge: negative/zero inside, positive outside."""
    distances = [
        point_to_segment_distance(point, polygon[index - 1], polygon[index])
        for index in range(len(polygon))
    ]
    distance = min(distances)
    return -distance if point_in_polygon(point, polygon) else distance


def normalized_to_pixels(
    points: Sequence[Point], width: int, height: int
) -> list[tuple[int, int]]:
    """Convert normalized coordinates to integer pixel coordinates."""
    return [
        (int(round(x * (width - 1))), int(round(y * (height - 1))))
        for x, y in points
    ]
