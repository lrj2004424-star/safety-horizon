import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from safety_monitor.camera_device import (
    CameraDevice,
    is_live_source,
    is_network_stream_source,
    redact_source,
)


class CameraDeviceTests(unittest.TestCase):
    def test_network_stream_is_live_and_credentials_are_redacted(self) -> None:
        source = "rtsp://operator:secret@192.0.2.10:554/Streaming/channels/3201?token=private"
        self.assertTrue(is_network_stream_source(source))
        self.assertTrue(is_live_source(source))
        safe = str(redact_source(source))
        self.assertIn("***:***@192.0.2.10:554", safe)
        self.assertNotIn("operator", safe)
        self.assertNotIn("secret", safe)
        self.assertNotIn("private", safe)

    def test_local_video_is_not_live(self) -> None:
        self.assertFalse(is_live_source("/tmp/replay.mp4"))
        self.assertTrue(is_live_source(0))

    def test_local_video_uses_same_read_interface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiny.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            for level in (20, 80, 140):
                writer.write(np.full((48, 64, 3), level, dtype=np.uint8))
            writer.release()
            device = CameraDevice(str(path))
            try:
                first = device.read_observation()
                self.assertTrue(first.ok)
                self.assertEqual(first.sequence, 0)
                self.assertEqual(first.frame.shape[:2], (48, 64))
            finally:
                device.release()


if __name__ == "__main__":
    unittest.main()
