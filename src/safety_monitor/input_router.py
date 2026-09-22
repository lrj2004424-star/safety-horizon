"""Hot-switch between the live EZVIZ window and labelled local test clips.

The router keeps the main vision loop unchanged while making the TouchDesigner
test-mode switch real.  Test files are restricted to ``test_videos`` inside
the project and loop at end-of-file.  The currently selected mode is exposed
to the status writer so simulation samples never contaminate field metrics or
authorize an audible actuator.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import cv2

from .camera_device import CameraDevice, CameraRead


class VisionInputRouter:
    """Camera-compatible input selected by one atomically written JSON file."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        mode_path: str | Path,
        live_factory: Callable[[], object],
        fps: float,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.mode_path = Path(mode_path).expanduser().resolve()
        self.test_root = (self.project_root / "test_videos").resolve()
        self.live_factory = live_factory
        self.fps = max(1.0, float(fps))
        self.width = 1280
        self.height = 720
        self._device: object | None = None
        self._mode = ""
        self._test_path: Path | None = None
        self._mode_mtime_ns = -1
        self._sequence = 0
        self._last_test_frame_s = 0.0
        self._refresh_mode(force=True)

    @property
    def mode(self) -> str:
        return self._mode or "LIVE"

    @property
    def test_path(self) -> Path | None:
        return self._test_path

    def status_context(self) -> dict[str, object]:
        """Safe metadata merged into the image-free vision status payload."""
        return {
            "input_mode": self.mode,
            "simulation": self.mode == "TEST",
            "physical_actuator_allowed": self.mode == "LIVE",
            "test_video_name": self._test_path.name if self._test_path else None,
        }

    def read_observation(self) -> CameraRead:
        self._refresh_mode()
        if self._device is None:
            return self._failure(
                "test_video_not_selected" if self.mode == "TEST" else "live_capture_unavailable"
            )
        if self.mode == "TEST":
            # A local file can otherwise run hundreds of frames per second and
            # make temporal fatigue signals meaningless.  Pace it at the same
            # target rate as the live capture without accumulating delay.
            target = 1.0 / self.fps
            now = time.monotonic()
            remaining = target - (now - self._last_test_frame_s)
            if remaining > 0:
                time.sleep(min(remaining, target))
            self._last_test_frame_s = time.monotonic()
        read = self._device.read_observation()  # type: ignore[attr-defined]
        if self.mode == "TEST" and (not read.ok or read.frame is None):
            # Loop a finite, labelled test clip deterministically.
            try:
                self._device.set(cv2.CAP_PROP_POS_FRAMES, 0)  # type: ignore[attr-defined]
                read = self._device.read_observation()  # type: ignore[attr-defined]
            except Exception:
                pass
        if read.ok and read.frame is not None:
            self.width = int(read.frame.shape[1])
            self.height = int(read.frame.shape[0])
            result = CameraRead(
                True,
                read.frame,
                read.captured_monotonic_s,
                self._sequence,
                read.reconnect_count,
                "test_video_frame_ok" if self.mode == "TEST" else read.reason,
            )
            self._sequence += 1
            return result
        return CameraRead(
            False,
            None,
            read.captured_monotonic_s,
            self._sequence,
            read.reconnect_count,
            read.reason,
        )

    def get(self, property_id: int) -> float:
        if property_id == cv2.CAP_PROP_FPS:
            return self.fps
        if property_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if property_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        try:
            return float(self._device.get(property_id)) if self._device is not None else 0.0  # type: ignore[attr-defined]
        except Exception:
            return 0.0

    def release(self) -> None:
        device, self._device = self._device, None
        if device is not None:
            try:
                device.release()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _refresh_mode(self, *, force: bool = False) -> None:
        try:
            modified = self.mode_path.stat().st_mtime_ns
        except OSError:
            modified = -1
        if not force and modified == self._mode_mtime_ns:
            return
        self._mode_mtime_ns = modified
        requested_mode = "LIVE"
        requested_path: Path | None = None
        try:
            payload = json.loads(self.mode_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                requested_mode = str(payload.get("mode", "LIVE")).strip().upper()
                if requested_mode not in {"LIVE", "TEST"}:
                    requested_mode = "LIVE"
                raw_path = payload.get("test_video")
                if requested_mode == "TEST" and raw_path:
                    candidate = Path(str(raw_path)).expanduser().resolve()
                    if (
                        self.test_root in candidate.parents
                        and candidate.is_file()
                        and candidate.suffix.lower() in {".mp4", ".mov", ".m4v", ".avi"}
                    ):
                        requested_path = candidate
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            requested_mode = "LIVE"
        if requested_mode == self._mode and requested_path == self._test_path and self._device is not None:
            return
        self.release()
        self._mode = requested_mode
        self._test_path = requested_path
        if requested_mode == "TEST":
            if requested_path is not None:
                device = CameraDevice(str(requested_path), reconnect_attempts=0)
                if device.is_opened():
                    self._device = device
                    file_fps = device.get(cv2.CAP_PROP_FPS)
                    if file_fps > 0:
                        self.fps = min(30.0, file_fps)
                else:
                    device.release()
        else:
            try:
                self._device = self.live_factory()
            except Exception:
                self._device = None

    def _failure(self, reason: str) -> CameraRead:
        return CameraRead(False, None, time.monotonic(), self._sequence, 0, reason)

