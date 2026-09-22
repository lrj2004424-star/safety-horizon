import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import cv2

from safety_monitor.window_capture_device import WindowCaptureDevice, select_fallback_window


class WindowCaptureDeviceTests(unittest.TestCase):
    def test_fallback_selects_ezviz_and_excludes_touchdesigner(self) -> None:
        selected = select_fallback_window(
            [
                {
                    "window_id": 20,
                    "owner": "TouchDesigner",
                    "title": "ezviz_safety_touchdesigner.toe",
                    "width": 1800,
                    "height": 1200,
                },
                {
                    "window_id": 10,
                    "owner": "腾讯应用宝-移动应用引",
                    "title": "萤石云视频",
                    "width": 465,
                    "height": 863,
                },
            ],
            title_contains="萤石云视频",
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["window_id"], 10)

    @unittest.skipIf(os.name == "nt", "macOS native helper uses POSIX executable pipes")
    def test_reads_fixed_bgra_frame_from_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "fake_capture.py"
            helper.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import struct
                    import sys
                    import time

                    width, height, fps = 4, 2, 12
                    sys.stdout.buffer.write(b"EZV1" + struct.pack("<III", width, height, fps))
                    sys.stdout.buffer.write(bytes([10, 20, 30, 255]) * width * height)
                    sys.stdout.buffer.flush()
                    time.sleep(1)
                    """
                ),
                encoding="utf-8",
            )
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
            device = WindowCaptureDevice(
                helper,
                bundle_id="test.bundle",
                startup_timeout_s=5,
            )
            try:
                self.assertTrue(device.is_opened())
                self.assertEqual(device.get(cv2.CAP_PROP_FRAME_WIDTH), 4)
                self.assertEqual(device.get(cv2.CAP_PROP_FRAME_HEIGHT), 2)
                observation = device.read_observation()
                self.assertTrue(observation.ok)
                self.assertEqual(observation.frame.shape, (2, 4, 3))
                self.assertEqual(observation.frame[0, 0].tolist(), [10, 20, 30])
            finally:
                device.release()

    def test_composited_fallback_skips_streaming_helper_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "fake_capture"
            helper.write_text("must not execute", encoding="utf-8")
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
            lister = Path(directory) / "ezviz_window_list"
            lister.write_text("[]", encoding="utf-8")
            lister.chmod(lister.stat().st_mode | stat.S_IXUSR)
            device = WindowCaptureDevice(
                helper,
                bundle_id="test.bundle",
                prefer_composited_fallback=True,
            )
            try:
                self.assertTrue(device.is_opened())
                self.assertIsNone(device._process)
            finally:
                device.release()

    def test_rejects_invalid_capture_rate(self) -> None:
        with self.assertRaises(ValueError):
            WindowCaptureDevice(
                "/does/not/matter",
                bundle_id="test.bundle",
                fps=31,
            )

    def test_rejects_invalid_output_aspect_ratio(self) -> None:
        with self.assertRaises(ValueError):
            WindowCaptureDevice(
                "/does/not/matter",
                bundle_id="test.bundle",
                output_aspect_ratio=0,
            )


if __name__ == "__main__":
    unittest.main()
