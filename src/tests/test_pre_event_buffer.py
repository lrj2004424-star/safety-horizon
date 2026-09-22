import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import cv2
import numpy as np

from safety_monitor.pre_event_buffer import ReviewClipBuffer


class FakeClock:
    def __init__(self, monotonic_s: float = 0.0, wall_s: float = 1_800_000_000.0) -> None:
        self.monotonic_s = monotonic_s
        self.wall_s = wall_s

    def monotonic(self) -> float:
        return self.monotonic_s

    def wall(self) -> float:
        return self.wall_s + self.monotonic_s


def frame(level: int, size: tuple[int, int] = (64, 48)) -> np.ndarray:
    width, height = size
    image = np.full((height, width, 3), level, dtype=np.uint8)
    cv2.putText(
        image,
        str(level),
        (3, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.3,
        (255 - level, 80, 20),
        1,
        cv2.LINE_AA,
    )
    return image


class ReviewClipBufferTests(unittest.TestCase):
    def test_defaults_are_five_seconds_before_and_after(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(directory, station_id="station 01")
            try:
                self.assertEqual(buffer.pre_seconds, 5.0)
                self.assertEqual(buffer.post_seconds, 5.0)
                self.assertEqual(buffer.sample_fps, 12.0)
            finally:
                buffer.close()

    def test_rejects_invalid_configuration_and_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                ReviewClipBuffer(directory, station_id="station", pre_seconds=0)
            with self.assertRaises(ValueError):
                ReviewClipBuffer(directory, station_id="station", sample_fps=61)
            with self.assertRaises(ValueError):
                ReviewClipBuffer(directory, station_id="station", jpeg_quality=30)
            buffer = ReviewClipBuffer(directory, station_id="station")
            try:
                with self.assertRaises(ValueError):
                    buffer.offer(np.zeros((10, 10), dtype=np.uint8))
                with self.assertRaises(ValueError):
                    buffer.offer(np.zeros((10, 10, 3), dtype=np.float32))
            finally:
                buffer.close()

    def test_ring_is_time_bounded_and_contains_jpeg_bytes(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="station",
                pre_seconds=1.0,
                post_seconds=1.0,
                sample_fps=4.0,
                clock=clock.monotonic,
                wall_clock=clock.wall,
            )
            try:
                for index in range(9):
                    timestamp = index * 0.25
                    buffer.offer(
                        frame(index * 12),
                        captured_monotonic_s=timestamp,
                        captured_wall_time_s=clock.wall_s + timestamp,
                        source_sequence=index,
                    )
                    self.assertTrue(buffer.wait_until_buffered(min(index + 1, 5)))
                self.assertLessEqual(buffer.buffered_frame_count, 6)
                self.assertGreater(buffer.buffered_jpeg_bytes, 0)
            finally:
                buffer.close()

    def test_builds_exact_time_based_pre_and_post_clip(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="station 01",
                pre_seconds=1.0,
                post_seconds=1.0,
                sample_fps=4.0,
                output_size=(64, 48),
                max_frame_gap_s=0.26,
                clock=clock.monotonic,
                wall_clock=clock.wall,
            )
            try:
                for index, timestamp in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
                    buffer.offer(
                        frame(20 + index * 10),
                        captured_monotonic_s=timestamp,
                        captured_wall_time_s=clock.wall_s + timestamp,
                        source_sequence=index,
                        telemetry={"risk_score": index * 10},
                    )
                    self.assertTrue(buffer.wait_until_buffered(index + 1))

                pending = buffer.trigger(
                    state="WARNING",
                    score=58,
                    reason="trend",
                    triggered_monotonic_s=1.0,
                    triggered_wall_time_s=clock.wall_s + 1.0,
                    context={"station": "left"},
                )
                self.assertEqual(pending.status, "CAPTURING")

                for index, timestamp in enumerate((1.25, 1.5, 1.75, 2.0), start=5):
                    buffer.offer(
                        frame(20 + index * 10),
                        captured_monotonic_s=timestamp,
                        captured_wall_time_s=clock.wall_s + timestamp,
                        source_sequence=index,
                        telemetry={"risk_score": 58},
                    )
                    self.assertTrue(buffer.wait_until_buffered(5))
                self.assertEqual(buffer.flush_due(2.01), 1)
                completed = buffer.wait_for_event(pending.event_id)

                self.assertEqual(completed.status, "READY")
                self.assertEqual(completed.frame_count, 8)
                self.assertAlmostEqual(completed.duration_seconds, 2.0)
                self.assertAlmostEqual(completed.actual_pre_seconds, 1.0)
                self.assertGreaterEqual(completed.actual_post_seconds, 0.75)
                self.assertEqual(completed.gap_frame_count, 0)
                self.assertTrue(completed.clip_complete)
                self.assertTrue(completed.clip_path.is_file())
                self.assertTrue(completed.timeline_path.is_file())
                self.assertTrue(completed.thumbnail_path.is_file())
                self.assertEqual(len(completed.sha256), 64)

                capture = cv2.VideoCapture(str(completed.clip_path))
                self.assertTrue(capture.isOpened())
                self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS), 4.0)
                self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 8)
                capture.release()

                timeline = json.loads(completed.timeline_path.read_text(encoding="utf-8"))
                self.assertEqual(timeline["trigger_frame_index"], 4)
                self.assertEqual(len(timeline["frames"]), 8)
                self.assertIn("telemetry", timeline["frames"][0])
                stored = json.loads(completed.metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(stored["event_id"], completed.event_id)
                self.assertEqual(stored["status"], "READY")
            finally:
                buffer.close()

    def test_higher_risk_trigger_merges_and_extends_event(self) -> None:
        clock = FakeClock(monotonic_s=10.0)
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="left",
                pre_seconds=5.0,
                post_seconds=5.0,
                sample_fps=4.0,
                clock=clock.monotonic,
                wall_clock=clock.wall,
            )
            try:
                first = buffer.trigger(
                    state="FATIGUE_TREND",
                    score=55,
                    reason="trend",
                    triggered_monotonic_s=10.0,
                )
                second = buffer.trigger(
                    state="HIGH_RISK",
                    score=84,
                    reason="risk_persisted",
                    triggered_monotonic_s=12.0,
                )
                self.assertEqual(first.event_id, second.event_id)
                self.assertEqual(second.state, "HIGH_RISK")
                self.assertEqual(second.score, 84)
                self.assertEqual(second.trigger_count, 2)
                self.assertEqual(second.merged_trigger_count, 1)
                self.assertAlmostEqual(buffer.active_event.last_triggered_monotonic_s, 12.0)
                self.assertEqual(buffer.flush_due(16.99), 0)
                self.assertEqual(buffer.flush_due(17.0), 1)
            finally:
                buffer.close()

    def test_trigger_after_post_window_creates_new_event(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="left",
                pre_seconds=1.0,
                post_seconds=1.0,
                sample_fps=2.0,
                clock=clock.monotonic,
                wall_clock=clock.wall,
            )
            try:
                first = buffer.trigger(
                    state="WARNING", score=50, reason="one", triggered_monotonic_s=1.0
                )
                second = buffer.trigger(
                    state="WARNING", score=52, reason="two", triggered_monotonic_s=2.1
                )
                self.assertNotEqual(first.event_id, second.event_id)
                self.assertEqual(second.trigger_count, 1)
            finally:
                buffer.close()

    def test_offer_remains_non_blocking_when_jpeg_worker_is_slow(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_encoder(image: np.ndarray, quality: int) -> bytes:
            started.set()
            release.wait(timeout=2.0)
            ok, encoded = cv2.imencode(".jpg", image)
            self.assertTrue(ok)
            return encoded.tobytes()

        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="left",
                input_queue_size=1,
                jpeg_encoder=slow_encoder,
            )
            try:
                buffer.offer(frame(1), captured_monotonic_s=1.0)
                self.assertTrue(started.wait(timeout=1.0))
                before = time.perf_counter()
                for index in range(100):
                    buffer.offer(frame(index % 255), captured_monotonic_s=2.0 + index * 0.1)
                elapsed = time.perf_counter() - before
                self.assertLess(elapsed, 0.15)
                self.assertGreater(buffer.dropped_input_frames, 0)
            finally:
                release.set()
                buffer.close()

    def test_missing_frames_produce_failed_metadata_without_partial_clip(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="left",
                pre_seconds=1.0,
                post_seconds=1.0,
                sample_fps=2.0,
                clock=clock.monotonic,
                wall_clock=clock.wall,
            )
            try:
                event = buffer.trigger(
                    state="UNCERTAIN",
                    score=0,
                    reason="capture_unavailable",
                    triggered_monotonic_s=1.0,
                )
                self.assertEqual(buffer.flush_due(2.0), 1)
                completed = buffer.wait_for_event(event.event_id)
                self.assertEqual(completed.status, "FAILED")
                self.assertEqual(completed.error, "event_contains_no_video_frames")
                self.assertIsNone(completed.clip_path)
                self.assertTrue(completed.metadata_path.is_file())
            finally:
                buffer.close()

    def test_shutdown_finalizes_active_event_as_truncated(self) -> None:
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            buffer = ReviewClipBuffer(
                directory,
                station_id="left",
                pre_seconds=0.5,
                post_seconds=1.0,
                sample_fps=2.0,
                output_size=(64, 48),
                clock=clock.monotonic,
                wall_clock=clock.wall,
            )
            buffer.offer(frame(60), captured_monotonic_s=0.0)
            self.assertTrue(buffer.wait_until_buffered(1))
            event = buffer.trigger(
                state="WARNING", score=60, reason="shutdown", triggered_monotonic_s=0.0
            )
            buffer.close(wait=True)
            completed = buffer.get_event(event.event_id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status, "READY")
            self.assertTrue(completed.truncated)
            self.assertFalse(completed.clip_complete)


if __name__ == "__main__":
    unittest.main()
