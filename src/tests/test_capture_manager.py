import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from ezviz_monitor import build_rtsp_url
from safety_monitor.capture_manager import CaptureManager, parse_capture_states


class CaptureManagerTests(unittest.TestCase):
    def test_capture_state_parser_is_strict(self) -> None:
        self.assertEqual(parse_capture_states("warning, DANGER"), {"WARNING", "DANGER"})
        self.assertEqual(
            parse_capture_states("fatigue_trend,HIGH_RISK"),
            {"FATIGUE_TREND", "HIGH_RISK"},
        )
        with self.assertRaises(ValueError):
            parse_capture_states("WARNING,NOT_A_STATE")

    def test_manual_snapshot_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frame = np.full((48, 64, 3), 140, dtype=np.uint8)
            manager = CaptureManager(
                directory,
                station_id="pilot 01",
                fps=10,
                frame_size=(64, 48),
            )
            path = manager.capture_snapshot(
                frame,
                state="WARNING",
                reason="test_reason",
            )
            manager.close()
            self.assertTrue(path.exists())
            self.assertIsNotNone(cv2.imread(str(path)))
            payload = json.loads((Path(directory) / "manifest.ndjson").read_text(encoding="utf-8"))
            self.assertEqual(payload["event_type"], "snapshot_saved")
            self.assertTrue(payload["face_blurred"])
            self.assertFalse(payload["identity_recognition_used"])
            self.assertNotIn("password", payload)

    def test_event_transition_starts_and_completes_clip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            manager = CaptureManager(
                directory,
                station_id="pilot",
                fps=10,
                frame_size=(64, 48),
                auto_states=("WARNING",),
                event_clip_seconds=0.2,
            )
            paths = manager.handle_transition(
                frame,
                state="WARNING",
                reason="test_warning",
                timestamp_s=1.0,
                transitioned=True,
            )
            self.assertEqual(len(paths), 2)
            self.assertTrue(manager.recording)
            manager.write(frame, 1.0)
            manager.write(frame, 1.2)
            self.assertFalse(manager.recording)
            clip = paths[1]
            self.assertTrue(clip.exists())
            events = [
                json.loads(line)
                for line in (Path(directory) / "manifest.ndjson").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1]["event_type"], "clip_completed")

    def test_rtsp_url_encodes_credentials_and_channel(self) -> None:
        url = build_rtsp_url(
            host="192.168.1.20",
            port=554,
            username="admin@example",
            password="p:a/s#word",
            channel=32,
            stream=1,
        )
        self.assertEqual(
            url,
            "rtsp://admin%40example:p%3Aa%2Fs%23word@192.168.1.20:554/Streaming/channels/3201",
        )


if __name__ == "__main__":
    unittest.main()
