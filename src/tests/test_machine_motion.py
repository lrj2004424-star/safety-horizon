import unittest

import numpy as np

from safety_monitor.config import MachineConfig
from safety_monitor.machine_motion import MachineMotionEstimator, MachineState


class MachineMotionTests(unittest.TestCase):
    @staticmethod
    def config(*, calibrated: bool) -> MachineConfig:
        return MachineConfig(
            motion_roi=((0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)),
            reference_roi=((0.0, 0.0), (0.2, 0.0), (0.2, 0.2), (0.0, 0.2)),
            global_motion_threshold=2.0,
            min_motion_pixels=0.03,
            vertical_motion_threshold=0.4,
            smoothing=1.0,
            direction_hold_frames=2,
            min_directional_coherence=0.55,
            calibrated_with_empty_cycles=calibrated,
            exclusion_radius_px=16,
            max_excluded_fraction=0.45,
        )

    def test_warmup_is_uncertain_not_static(self) -> None:
        result = MachineMotionEstimator(self.config(calibrated=True)).update(
            np.zeros((120, 160, 3), dtype=np.uint8)
        )
        self.assertEqual(result.state, MachineState.UNCERTAIN)
        self.assertFalse(result.quality_ok)

    def test_uncalibrated_roi_remains_uncertain_after_warmup(self) -> None:
        estimator = MachineMotionEstimator(self.config(calibrated=False))
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        estimator.update(frame)
        result = estimator.update(frame)
        self.assertEqual(result.state, MachineState.UNCERTAIN)
        self.assertFalse(result.quality_ok)
        self.assertIn("uncalibrated_roi", result.reason)

    def test_heavy_hand_exclusion_forces_uncertain(self) -> None:
        estimator = MachineMotionEstimator(self.config(calibrated=True))
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        estimator.update(frame)
        points = [(x / 10, y / 10) for x in range(3, 8) for y in range(3, 8)]
        result = estimator.update(frame, points)
        self.assertEqual(result.state, MachineState.UNCERTAIN)
        self.assertFalse(result.quality_ok)
        self.assertEqual(result.reason, "hand_occludes_machine_roi")


if __name__ == "__main__":
    unittest.main()
