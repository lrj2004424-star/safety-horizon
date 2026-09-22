import unittest

from safety_monitor.geometry import (
    point_in_polygon,
    point_to_segment_distance,
    signed_distance_to_polygon,
)


class GeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.square = ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))

    def test_inside_boundary_and_outside(self) -> None:
        self.assertTrue(point_in_polygon((0.5, 0.5), self.square))
        self.assertTrue(point_in_polygon((0.25, 0.5), self.square))
        self.assertFalse(point_in_polygon((0.1, 0.5), self.square))

    def test_signed_distance_convention(self) -> None:
        self.assertLess(signed_distance_to_polygon((0.5, 0.5), self.square), 0)
        self.assertAlmostEqual(
            signed_distance_to_polygon((0.1, 0.5), self.square), 0.15
        )

    def test_segment_distance(self) -> None:
        self.assertAlmostEqual(
            point_to_segment_distance((0.5, 0.5), (0.0, 0.0), (1.0, 0.0)),
            0.5,
        )


if __name__ == "__main__":
    unittest.main()
