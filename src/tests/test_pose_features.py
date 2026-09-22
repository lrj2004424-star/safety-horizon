import unittest

from safety_monitor.pose_tracker import PosePoint, build_pose_observation


class PoseFeatureTests(unittest.TestCase):
    def test_upper_body_angles_and_center(self) -> None:
        points = [PosePoint(0.5, 0.5, 1.0, 1.0) for _ in range(33)]
        points[11] = PosePoint(0.4, 0.3, 1.0, 1.0)
        points[12] = PosePoint(0.6, 0.3, 1.0, 1.0)
        points[13] = PosePoint(0.4, 0.5, 1.0, 1.0)
        points[14] = PosePoint(0.6, 0.5, 1.0, 1.0)
        points[15] = PosePoint(0.4, 0.7, 1.0, 1.0)
        points[16] = PosePoint(0.6, 0.7, 1.0, 1.0)
        points[23] = PosePoint(0.45, 0.75, 1.0, 1.0)
        points[24] = PosePoint(0.65, 0.75, 1.0, 1.0)
        result = build_pose_observation(tuple(points))
        self.assertTrue(result.upper_body_visible)
        self.assertAlmostEqual(result.left_elbow_angle or 0, 180.0)
        self.assertAlmostEqual(result.torso_center[0], 0.525)
        self.assertIsNotNone(result.torso_lean_deg)


if __name__ == "__main__":
    unittest.main()
