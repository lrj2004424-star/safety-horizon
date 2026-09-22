"""OpenCV camera device with bounded reconnect and health metadata."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import cv2
import numpy as np


NETWORK_STREAM_SCHEMES = frozenset({"rtsp", "rtsps", "http", "https", "udp", "tcp"})


def is_network_stream_source(source: int | str) -> bool:
    """Return true for a live URL without opening or logging it."""
    if not isinstance(source, str):
        return False
    return urlsplit(source).scheme.lower() in NETWORK_STREAM_SCHEMES


def is_live_source(source: int | str) -> bool:
    """Local camera indexes and network streams are live; video files are not."""
    return isinstance(source, int) or is_network_stream_source(source)


def redact_source(source: int | str) -> int | str:
    """Describe a source safely for logs without exposing credentials or tokens."""
    if not isinstance(source, str) or not is_network_stream_source(source):
        return source
    parsed = urlsplit(source)
    hostname = parsed.hostname or "stream-host"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    auth = "***:***@" if parsed.username is not None or parsed.password is not None else ""
    safe = SplitResult(
        parsed.scheme,
        f"{auth}{hostname}{port}",
        parsed.path,
        "..." if parsed.query else "",
        "",
    )
    return urlunsplit(safe)


@dataclass(frozen=True)
class CameraRead:
    ok: bool
    frame: np.ndarray | None
    captured_monotonic_s: float
    sequence: int
    reconnect_count: int
    reason: str


class CameraDevice:
    """Treat a local file and a live camera through one small, testable API."""

    def __init__(
        self,
        source: int | str,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        reconnect_attempts: int = 2,
    ) -> None:
        self.source = source
        self.is_network_stream = is_network_stream_source(source)
        self.is_live = is_live_source(source)
        self.width = width
        self.height = height
        self.fps = fps
        self.reconnect_attempts = max(0, int(reconnect_attempts))
        self._capture: cv2.VideoCapture | None = None
        self._sequence = 0
        self._reconnect_count = 0
        self._open()

    def _open(self) -> None:
        if self._capture is not None:
            self._capture.release()
        capture: cv2.VideoCapture
        if self.is_network_stream and hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            # Open/read timeouts are constructor parameters for FFmpeg. Falling
            # back keeps compatibility with OpenCV builds lacking that backend.
            capture = cv2.VideoCapture(
                self.source,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                    5000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                    3000,
                ],
            )
            if not capture.isOpened():
                capture.release()
                capture = cv2.VideoCapture(self.source)
        else:
            capture = cv2.VideoCapture(self.source)
        if self.is_live:
            if self.width:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            if self.height:
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            if self.fps:
                capture.set(cv2.CAP_PROP_FPS, self.fps)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._capture = capture

    def is_opened(self) -> bool:
        return bool(self._capture is not None and self._capture.isOpened())

    def read_observation(self) -> CameraRead:
        attempts = self.reconnect_attempts + 1 if self.is_live else 1
        for attempt in range(attempts):
            if self._capture is None or not self._capture.isOpened():
                if not self.is_live:
                    break
                self._open()
            assert self._capture is not None
            ok, frame = self._capture.read()
            captured = time.monotonic()
            if ok and frame is not None:
                sequence = self._sequence
                self._sequence += 1
                return CameraRead(
                    True,
                    frame,
                    captured,
                    sequence,
                    self._reconnect_count,
                    "camera_frame_ok",
                )
            if not self.is_live or attempt + 1 >= attempts:
                break
            self._capture.release()
            self._capture = None
            self._reconnect_count += 1
        return CameraRead(
            False,
            None,
            time.monotonic(),
            self._sequence,
            self._reconnect_count,
            "camera_read_failed_after_reconnect",
        )

    def read(self) -> tuple[bool, np.ndarray | None]:
        observation = self.read_observation()
        return observation.ok, observation.frame

    def get(self, property_id: int) -> float:
        return self._capture.get(property_id) if self._capture is not None else 0.0

    def set(self, property_id: int, value: float) -> bool:
        return bool(self._capture is not None and self._capture.set(property_id, value))

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def __enter__(self) -> "CameraDevice":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def probe_camera_indices(max_index: int = 6) -> list[dict[str, float | int]]:
    """Return devices that can provide an actual frame; no frames are saved."""
    devices: list[dict[str, float | int]] = []
    for index in range(max(0, max_index)):
        capture = cv2.VideoCapture(index)
        ok, frame = capture.read() if capture.isOpened() else (False, None)
        if ok and frame is not None:
            devices.append(
                {
                    "index": index,
                    "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": float(capture.get(cv2.CAP_PROP_FPS)),
                }
            )
        capture.release()
    return devices
