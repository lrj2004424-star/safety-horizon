"""Opt-in, face-blurred snapshots and short event clips for the live monitor."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


KNOWN_STATES = frozenset(
    {
        "NORMAL",
        "ATTENTION",
        "WARNING",
        "DANGER",
        "UNCERTAIN",
        "FAULT",
        "OBSERVING",
        "ACTIVE",
        "FATIGUE_TREND",
        "HIGH_RISK",
    }
)


def parse_capture_states(raw: str | Iterable[str]) -> frozenset[str]:
    """Parse and validate a comma-separated set of joint-state names."""
    values = raw.split(",") if isinstance(raw, str) else list(raw)
    states = frozenset(value.strip().upper() for value in values if value.strip())
    unknown = states - KNOWN_STATES
    if unknown:
        raise ValueError(f"Unknown auto-capture states: {', '.join(sorted(unknown))}")
    return states


class CaptureManager:
    """Write explicit derived captures without storing stream credentials."""

    def __init__(
        self,
        root: str | Path,
        *,
        station_id: str,
        fps: float,
        frame_size: tuple[int, int],
        auto_states: Iterable[str] = ("WARNING", "DANGER"),
        event_clip_seconds: float = 10.0,
        jpeg_quality: int = 92,
    ) -> None:
        width, height = frame_size
        if width < 1 or height < 1:
            raise ValueError("frame_size must contain positive width and height")
        if fps <= 0:
            raise ValueError("fps must be positive")
        if event_clip_seconds <= 0:
            raise ValueError("event_clip_seconds must be positive")
        if not 40 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 40 and 100")
        self.root = Path(root).expanduser().resolve()
        self.snapshots_dir = self.root / "snapshots"
        self.clips_dir = self.root / "clips"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.ndjson"
        self.station_id = _safe_component(station_id)
        self.fps = float(fps)
        self.frame_size = (int(width), int(height))
        self.auto_states = parse_capture_states(auto_states)
        self.event_clip_seconds = float(event_clip_seconds)
        self.jpeg_quality = int(jpeg_quality)
        self._writer: cv2.VideoWriter | None = None
        self._recording_path: Path | None = None
        self._recording_mode: str | None = None
        self._recording_state: str | None = None
        self._recording_reason: str | None = None
        self._auto_until_s: float | None = None

    @property
    def recording(self) -> bool:
        return self._writer is not None

    @property
    def recording_mode(self) -> str | None:
        return self._recording_mode

    @property
    def recording_path(self) -> Path | None:
        return self._recording_path

    def capture_snapshot(
        self,
        frame: np.ndarray,
        *,
        state: str,
        reason: str,
        kind: str = "manual",
    ) -> Path:
        self._validate_frame(frame)
        state_name = _state_name(state)
        path = self.snapshots_dir / self._filename(kind, state_name, ".jpg")
        ok = cv2.imwrite(
            str(path),
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
        )
        if not ok:
            raise RuntimeError(f"Could not write capture: {path}")
        self._append_manifest(
            "snapshot_saved",
            path,
            state=state_name,
            reason=reason,
            capture_kind=kind,
        )
        return path

    def handle_transition(
        self,
        frame: np.ndarray,
        *,
        state: str,
        reason: str,
        timestamp_s: float,
        transitioned: bool,
    ) -> tuple[Path, ...]:
        """Capture once when a configured state is entered and start/extend its clip."""
        state_name = _state_name(state)
        if not transitioned or state_name not in self.auto_states:
            return ()
        snapshot = self.capture_snapshot(
            frame,
            state=state_name,
            reason=reason,
            kind="event",
        )
        if self._recording_mode == "manual":
            return (snapshot,)
        if self._recording_mode == "event":
            self._auto_until_s = max(
                self._auto_until_s or timestamp_s,
                timestamp_s + self.event_clip_seconds,
            )
            return (snapshot,)
        clip = self._start_recording("event", state_name, reason)
        self._auto_until_s = timestamp_s + self.event_clip_seconds
        return (snapshot, clip)

    def toggle_manual_recording(self, *, state: str, reason: str) -> Path | None:
        """Start a manual clip, or stop whichever clip is currently recording."""
        if self.recording:
            self.stop_recording()
            return None
        return self._start_recording("manual", _state_name(state), reason)

    def write(self, frame: np.ndarray, timestamp_s: float) -> None:
        if self._writer is None:
            return
        self._validate_frame(frame)
        self._writer.write(frame)
        if (
            self._recording_mode == "event"
            and self._auto_until_s is not None
            and timestamp_s >= self._auto_until_s
        ):
            self.stop_recording()

    def stop_recording(self) -> Path | None:
        if self._writer is None:
            return None
        writer, path = self._writer, self._recording_path
        mode, state, reason = self._recording_mode, self._recording_state, self._recording_reason
        self._writer = None
        self._recording_path = None
        self._recording_mode = None
        self._recording_state = None
        self._recording_reason = None
        self._auto_until_s = None
        writer.release()
        assert path is not None
        self._append_manifest(
            "clip_completed",
            path,
            state=state or "UNKNOWN",
            reason=reason or "",
            capture_kind=mode or "unknown",
        )
        return path

    def close(self) -> None:
        self.stop_recording()

    def _start_recording(self, mode: str, state: str, reason: str) -> Path:
        path = self.clips_dir / self._filename(mode, state, ".mp4")
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            self.fps,
            self.frame_size,
        )
        if not writer.isOpened():
            writer.release()
            raise RuntimeError(f"Could not create capture clip: {path}")
        self._writer = writer
        self._recording_path = path
        self._recording_mode = mode
        self._recording_state = state
        self._recording_reason = reason
        self._append_manifest(
            "clip_started",
            path,
            state=state,
            reason=reason,
            capture_kind=mode,
        )
        return path

    def _validate_frame(self, frame: np.ndarray) -> None:
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("capture frame must be a BGR color image")
        height, width = frame.shape[:2]
        if (width, height) != self.frame_size:
            raise ValueError(
                f"capture frame is {width}x{height}; expected {self.frame_size[0]}x{self.frame_size[1]}"
            )

    def _filename(self, kind: str, state: str, extension: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{self.station_id}_{_safe_component(kind)}_{_safe_component(state)}_{timestamp}{extension}"

    def _append_manifest(
        self,
        event_type: str,
        path: Path,
        *,
        state: str,
        reason: str,
        capture_kind: str,
    ) -> None:
        payload = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "station_id": self.station_id,
            "event_type": event_type,
            "capture_kind": capture_kind,
            "state": state,
            "reason": reason,
            "path": str(path.relative_to(self.root)),
            "face_blurred": True,
            "identity_recognition_used": False,
            "stream_credentials_stored": False,
        }
        with self.manifest_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _state_name(value: str | object) -> str:
    raw = getattr(value, "name", value)
    return str(raw).strip().upper() or "UNKNOWN"


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip()).strip("-")
    return cleaned[:64] or "capture"
