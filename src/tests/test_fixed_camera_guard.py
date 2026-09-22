import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from safety_monitor.config import FixedCameraConfig
from safety_monitor.fixed_camera_guard import FixedCameraGuard


class FixedCameraGuardTests(unittest.TestCase):
    @staticmethod
    def config() -> FixedCameraConfig:
        return FixedCameraConfig(
            enabled=True,
            reference_image=None,
            anchor_rois=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),),
            check_interval_frames=1,
            min_matches=12,
            max_translation_px=5.0,
            max_rotation_deg=1.0,
            max_scale_deviation=0.02,
        )

    @staticmethod
    def textured_frame() -> np.ndarray:
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        rng = np.random.default_rng(7)
        for x, y in rng.integers((8, 8), (312, 232), size=(180, 2)):
            cv2.circle(frame, (int(x), int(y)), 2, (255, 255, 255), -1)
        cv2.putText(frame, "FIXED", (70, 130), cv2.FONT_HERSHEY_DUPLEX, 1.2, (180, 180, 180), 2)
        return frame

    def test_same_view_is_stable_and_shift_is_rejected(self) -> None:
        reference = self.textured_frame()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            self.assertTrue(cv2.imwrite(str(path), reference))
            guard = FixedCameraGuard(self.config(), path)
            stable = guard.update(reference.copy())
            self.assertTrue(stable.stable)
            shifted = cv2.warpAffine(reference, np.float32([[1, 0, 14], [0, 1, 0]]), (320, 240))
            changed = guard.update(shifted)
            self.assertFalse(changed.stable)
            self.assertEqual(changed.reason, "fixed_camera_alignment_changed")


if __name__ == "__main__":
    unittest.main()
