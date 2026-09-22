"""Bounded, non-blocking pre/post-event review video buffer.

The safety monitor publishes privacy-filtered video frames continuously.  This
module samples those frames into a JPEG ring buffer, freezes a time-based
window when an alert occurs, and writes the review artefacts on a background
thread.  JPEG compression and MP4 writing therefore never run on the vision
thread.

``ReviewClipBuffer`` intentionally exposes two small APIs:

* ``offer`` / ``trigger`` are the typed API used by tests and new callers.
* ``offer_jpeg`` / ``start_event`` / ``tick`` / ``drain_completed`` preserve
  the file-sidecar contract used by :mod:`safety_officer_review`.

All frames held in memory are compressed bytes.  The class does not perform
identity recognition and marks every emitted clip as privacy filtered.
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import cv2
import numpy as np


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")
_FINAL_STATUSES = frozenset({"READY", "FAILED"})
_SEVERITY = {
    "ACTIVE": 0,
    "NORMAL": 0,
    "OBSERVING": 1,
    "UNCERTAIN": 2,
    "WARNING": 3,
    "FATIGUE_TREND": 3,
    "DANGER": 4,
    "HIGH_RISK": 4,
}


def _safe(value: str) -> str:
    result = _SAFE_COMPONENT.sub("-", str(value).strip()).strip("-.")
    return result[:120] or "event"


def _json_value(value: Any) -> Any:
    """Return a detached JSON-compatible value without trusting callers."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _default_jpeg_encoder(image: np.ndarray, quality: int) -> bytes:
    ok, encoded = cv2.imencode(
        ".jpg",
        image,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
    )
    if not ok:
        raise RuntimeError("jpeg_encoding_failed")
    return encoded.tobytes()


@dataclass(frozen=True)
class BufferedJpeg:
    """One compressed frame plus evidence needed for frame-by-frame review."""

    captured_monotonic_s: float
    captured_wall_time_s: float
    source_sequence: int
    data: bytes
    telemetry: dict[str, Any] = field(default_factory=dict)

    @property
    def captured_s(self) -> float:
        """Compatibility alias retained for the original sidecar."""
        return self.captured_monotonic_s


@dataclass
class ReviewClipEvent:
    """Mutable lifecycle record returned by :meth:`ReviewClipBuffer.trigger`."""

    event_id: str
    station_id: str
    state: str
    score: int
    reason: str
    triggered_monotonic_s: float
    triggered_wall_time_s: float
    last_triggered_monotonic_s: float
    window_start_monotonic_s: float
    deadline_monotonic_s: float
    day: str
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "CAPTURING"
    trigger_count: int = 1
    merged_trigger_count: int = 0
    truncated: bool = False
    clip_complete: bool = False
    frame_count: int = 0
    duration_seconds: float = 0.0
    actual_pre_seconds: float = 0.0
    actual_post_seconds: float = 0.0
    gap_frame_count: int = 0
    trigger_frame_index: int = 0
    clip_path: Path | None = None
    timeline_path: Path | None = None
    thumbnail_path: Path | None = None
    metadata_path: Path | None = None
    sha256: str = ""
    error: str | None = None
    _frames: list[BufferedJpeg] = field(default_factory=list, repr=False)
    _frame_keys: set[tuple[float, int]] = field(default_factory=set, repr=False)
    _merge_enabled: bool = field(default=True, repr=False)


@dataclass(frozen=True)
class _RawFrameJob:
    image: np.ndarray
    captured_monotonic_s: float
    captured_wall_time_s: float
    source_sequence: int
    telemetry: dict[str, Any]


class ReviewClipBuffer:
    """Keep a bounded JPEG pre-roll and asynchronously create review clips.

    Parameters with ``*_roll_s`` and ``fps`` names are compatibility aliases
    for the original sidecar. New code should use ``pre_seconds``,
    ``post_seconds`` and ``sample_fps``.
    """

    def __init__(
        self,
        output_root: str | Path,
        *,
        station_id: str = "upper_station",
        pre_seconds: float | None = None,
        post_seconds: float | None = None,
        sample_fps: float | None = None,
        output_size: tuple[int, int] | None = None,
        max_frame_gap_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        input_queue_size: int = 8,
        jpeg_encoder: Callable[[np.ndarray, int], bytes] | None = None,
        jpeg_quality: int = 82,
        # Backward-compatible aliases used by safety_officer_review.py.
        fps: float | None = None,
        pre_roll_s: float | None = None,
        post_roll_s: float | None = None,
        max_width: int = 960,
    ) -> None:
        chosen_pre = pre_roll_s if pre_roll_s is not None else pre_seconds
        chosen_post = post_roll_s if post_roll_s is not None else post_seconds
        chosen_fps = fps if fps is not None else sample_fps
        self.pre_seconds = float(5.0 if chosen_pre is None else chosen_pre)
        self.post_seconds = float(5.0 if chosen_post is None else chosen_post)
        self.sample_fps = float(12.0 if chosen_fps is None else chosen_fps)
        if not math.isfinite(self.pre_seconds) or self.pre_seconds <= 0.0:
            raise ValueError("pre_seconds must be greater than zero")
        if not math.isfinite(self.post_seconds) or self.post_seconds <= 0.0:
            raise ValueError("post_seconds must be greater than zero")
        if (
            not math.isfinite(self.sample_fps)
            or self.sample_fps <= 0.0
            or self.sample_fps > 60.0
        ):
            raise ValueError("sample_fps must be in (0, 60]")
        if not isinstance(jpeg_quality, int) or not 40 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be an integer in [40, 100]")
        if not isinstance(input_queue_size, int) or input_queue_size < 1:
            raise ValueError("input_queue_size must be a positive integer")
        if not isinstance(max_width, int) or max_width < 160:
            raise ValueError("max_width must be at least 160")
        if output_size is not None:
            if (
                not isinstance(output_size, tuple)
                or len(output_size) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 2
                    for value in output_size
                )
            ):
                raise ValueError("output_size must be a (width, height) tuple")
            output_size = (
                output_size[0] - output_size[0] % 2,
                output_size[1] - output_size[1] % 2,
            )
        chosen_gap = (
            (1.75 / self.sample_fps)
            if max_frame_gap_s is None
            else float(max_frame_gap_s)
        )
        if not math.isfinite(chosen_gap) or chosen_gap <= 0.0:
            raise ValueError("max_frame_gap_s must be greater than zero")
        if not callable(clock) or not callable(wall_clock):
            raise ValueError("clock and wall_clock must be callable")

        self.output_root = Path(output_root).expanduser().resolve()
        self.station_id = _safe(station_id)
        self.output_size = output_size
        self.max_frame_gap_s = chosen_gap
        self.max_width = max_width
        self.jpeg_quality = jpeg_quality
        self._clock = clock
        self._wall_clock = wall_clock
        self._jpeg_encoder = jpeg_encoder or _default_jpeg_encoder

        # Compatibility attributes retained for the existing sidecar.
        self.pre_roll_s = self.pre_seconds
        self.post_roll_s = self.post_seconds
        self.fps = self.sample_fps

        self._minimum_interval_s = 1.0 / self.sample_fps
        self._last_reserved_s = float("-inf")
        self._sequence = 0
        self._ring: deque[BufferedJpeg] = deque()
        self._ring_bytes = 0
        self._events: dict[str, ReviewClipEvent] = {}
        self._active: dict[str, ReviewClipEvent] = {}
        self._active_event_id: str | None = None
        self._completed_for_drain: deque[str] = deque()
        self._dropped_input_frames = 0
        self._submitted_frame_jobs = 0
        self._finished_frame_jobs = 0
        self._closed = False
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

        self._input_jobs: queue.Queue[_RawFrameJob | None] = queue.Queue(
            maxsize=input_queue_size
        )
        self._write_jobs: queue.Queue[ReviewClipEvent | None] = queue.Queue()
        self._encoder_thread = threading.Thread(
            target=self._encoder_loop,
            name="review-jpeg-encoder",
            daemon=True,
        )
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="review-clip-writer",
            daemon=True,
        )
        self._encoder_thread.start()
        self._writer_thread.start()

    @property
    def buffered_frame_count(self) -> int:
        with self._lock:
            return len(self._ring)

    @property
    def buffered_jpeg_bytes(self) -> int:
        with self._lock:
            return self._ring_bytes

    @property
    def dropped_input_frames(self) -> int:
        with self._lock:
            return self._dropped_input_frames

    @property
    def active_event(self) -> ReviewClipEvent | None:
        with self._lock:
            if self._active_event_id is None:
                return None
            return self._active.get(self._active_event_id)

    def offer(
        self,
        image: np.ndarray,
        *,
        captured_monotonic_s: float | None = None,
        captured_wall_time_s: float | None = None,
        source_sequence: int | None = None,
        telemetry: Mapping[str, Any] | None = None,
    ) -> bool:
        """Queue a BGR frame for JPEG compression without blocking the caller."""
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
            raise ValueError("image must be a uint8 numpy array")
        if (
            image.ndim != 3
            or image.shape[2] != 3
            or image.shape[0] < 1
            or image.shape[1] < 1
        ):
            raise ValueError("image must have shape (height, width, 3)")
        captured = (
            self._clock()
            if captured_monotonic_s is None
            else float(captured_monotonic_s)
        )
        wall = (
            self._wall_clock()
            if captured_wall_time_s is None
            else float(captured_wall_time_s)
        )
        if not math.isfinite(captured) or not math.isfinite(wall):
            raise ValueError("frame timestamps must be finite")
        with self._lock:
            if self._closed or not self._sample_is_due_locked(captured):
                return False
            sequence = self._next_sequence_locked(source_sequence)
            job = _RawFrameJob(
                image=np.ascontiguousarray(image).copy(),
                captured_monotonic_s=captured,
                captured_wall_time_s=wall,
                source_sequence=sequence,
                telemetry=dict(_json_value(dict(telemetry or {}))),
            )
            try:
                self._input_jobs.put_nowait(job)
            except queue.Full:
                self._dropped_input_frames += 1
                return False
            self._submitted_frame_jobs += 1
            self._last_reserved_s = captured
            return True

    def offer_jpeg(self, data: bytes, captured_s: float | None = None) -> bool:
        """Offer an already privacy-filtered JPEG (sidecar compatibility API)."""
        if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
            return False
        captured = self._clock() if captured_s is None else float(captured_s)
        if not math.isfinite(captured):
            return False
        with self._lock:
            if self._closed or not self._sample_is_due_locked(captured):
                return False
            sequence = self._next_sequence_locked(None)
            self._last_reserved_s = captured
            frame = BufferedJpeg(
                captured_monotonic_s=captured,
                captured_wall_time_s=float(self._wall_clock()),
                source_sequence=sequence,
                data=bytes(data),
                telemetry={},
            )
            self._accept_encoded_locked(frame)
            return True

    def wait_until_buffered(self, count: int, timeout: float = 2.0) -> bool:
        """Wait until at least ``count`` compressed frames are present."""
        if count <= 0:
            return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while len(self._ring) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def trigger(
        self,
        *,
        state: str,
        score: int | float,
        reason: str,
        triggered_monotonic_s: float | None = None,
        triggered_wall_time_s: float | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> ReviewClipEvent:
        """Create an event or merge an alert into the current alert episode."""
        triggered = (
            self._clock()
            if triggered_monotonic_s is None
            else float(triggered_monotonic_s)
        )
        wall = (
            self._wall_clock()
            if triggered_wall_time_s is None
            else float(triggered_wall_time_s)
        )
        if not math.isfinite(triggered) or not math.isfinite(wall):
            raise ValueError("trigger timestamps must be finite")
        normalized_state = str(state).strip().upper() or "UNCERTAIN"
        numeric_score = max(0, min(100, int(round(float(score)))))
        with self._lock:
            if self._closed:
                raise RuntimeError("review clip buffer is closed")
            # A trigger after the post window starts a new event even when the
            # caller has not yet run flush_due().
            self._queue_expired_locked(triggered)
            active = self.active_event
            if (
                active is not None
                and active._merge_enabled
                and triggered <= active.deadline_monotonic_s + 1e-9
            ):
                active.trigger_count += 1
                active.merged_trigger_count += 1
                active.last_triggered_monotonic_s = triggered
                active.deadline_monotonic_s = triggered + self.post_seconds
                old_rank = _SEVERITY.get(active.state, 2)
                new_rank = _SEVERITY.get(normalized_state, 2)
                if new_rank > old_rank or numeric_score >= active.score:
                    active.state = normalized_state
                    active.score = numeric_score
                    active.reason = str(reason)
                    if context:
                        active.context.update(dict(_json_value(dict(context))))
                return active
            event_id = self._new_event_id(wall)
            return self._create_event_locked(
                event_id=event_id,
                station_id=self.station_id,
                state=normalized_state,
                score=numeric_score,
                reason=str(reason),
                triggered=triggered,
                wall=wall,
                context=context,
                day=None,
                merge_enabled=True,
            )

    def start_event(
        self,
        event_id: str,
        *,
        station_id: str,
        state: str,
        trigger_s: float | None = None,
        day: str | None = None,
    ) -> dict[str, Any]:
        """Start a specifically named event (sidecar compatibility API)."""
        triggered = self._clock() if trigger_s is None else float(trigger_s)
        event_key = _safe(event_id)
        with self._lock:
            if event_key in self._events:
                return self.recording_metadata(event_key)
            if self._closed:
                raise RuntimeError("review clip buffer is closed")
            self._queue_expired_locked(triggered)
            self._create_event_locked(
                event_id=event_key,
                station_id=_safe(station_id),
                state=str(state).strip().upper() or "UNCERTAIN",
                score=0,
                reason="sidecar_alert",
                triggered=triggered,
                wall=float(self._wall_clock()),
                context=None,
                day=day,
                merge_enabled=False,
            )
            return self.recording_metadata(event_key)

    def recording_metadata(self, event_id: str) -> dict[str, Any]:
        """Return the normalized mapping accepted by the review data contract."""
        event = self._events.get(_safe(event_id))
        if event is None:
            return self._compat_metadata(None)
        return self._compat_metadata(event)

    def flush_due(self, now_s: float | None = None) -> int:
        """Queue every event whose post window has elapsed for MP4 writing."""
        now = self._clock() if now_s is None else float(now_s)
        # Freeze the event only after every frame accepted before this call has
        # reached the compressed ring. New offers are not part of this barrier,
        # so a continuously running producer cannot starve finalization.
        with self._condition:
            barrier = self._submitted_frame_jobs
            while self._finished_frame_jobs < barrier:
                self._condition.wait()
            return self._queue_expired_locked(now)

    def tick(self, now_s: float | None = None) -> None:
        """Sidecar compatibility alias for :meth:`flush_due`."""
        self.flush_due(now_s)

    def wait_for_event(self, event_id: str, timeout: float = 8.0) -> ReviewClipEvent:
        """Wait for a queued event to reach READY or FAILED."""
        key = _safe(event_id)
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                event = self._events.get(key)
                if event is None:
                    raise KeyError(key)
                if event.status in _FINAL_STATUSES:
                    return event
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise TimeoutError(f"review clip {key} did not finish")
                self._condition.wait(remaining)

    def get_event(self, event_id: str) -> ReviewClipEvent | None:
        with self._lock:
            return self._events.get(_safe(event_id))

    def drain_completed(self) -> list[tuple[str, dict[str, Any]]]:
        """Drain completed mappings for the existing file-based sidecar."""
        with self._lock:
            output: list[tuple[str, dict[str, Any]]] = []
            while self._completed_for_drain:
                event_id = self._completed_for_drain.popleft()
                event = self._events.get(event_id)
                if event is not None:
                    output.append((event_id, self._compat_metadata(event)))
            return output

    def close(
        self,
        wait: bool = True,
        *,
        wait_s: float | None = None,
    ) -> None:
        """Stop workers and finalize active clips as explicitly truncated."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # Complete JPEG work first so accepted frames are not silently lost.
        if wait:
            self._input_jobs.join()
        self._put_sentinel(self._input_jobs)
        if wait:
            self._encoder_thread.join(timeout=wait_s)

        with self._lock:
            for event in list(self._active.values()):
                self._queue_event_locked(event, truncated=True)

        if wait:
            self._write_jobs.join()
        self._write_jobs.put(None)
        if wait:
            self._writer_thread.join(timeout=wait_s)

    def _sample_is_due_locked(self, captured: float) -> bool:
        return captured - self._last_reserved_s + 1e-9 >= self._minimum_interval_s

    def _next_sequence_locked(self, requested: int | None) -> int:
        if requested is None:
            value = self._sequence
            self._sequence += 1
            return value
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise ValueError("source_sequence must be an integer")
        self._sequence = max(self._sequence, requested + 1)
        return requested

    def _new_event_id(self, wall_s: float) -> str:
        stamp = datetime.fromtimestamp(wall_s, UTC).strftime("%Y%m%dT%H%M%S%f")[:-3]
        return _safe(f"{self.station_id}-{stamp}-{uuid.uuid4().hex[:8]}")

    def _create_event_locked(
        self,
        *,
        event_id: str,
        station_id: str,
        state: str,
        score: int,
        reason: str,
        triggered: float,
        wall: float,
        context: Mapping[str, Any] | None,
        day: str | None,
        merge_enabled: bool,
    ) -> ReviewClipEvent:
        start = triggered - self.pre_seconds
        event = ReviewClipEvent(
            event_id=event_id,
            station_id=station_id,
            state=state,
            score=score,
            reason=reason,
            triggered_monotonic_s=triggered,
            triggered_wall_time_s=wall,
            last_triggered_monotonic_s=triggered,
            window_start_monotonic_s=start,
            deadline_monotonic_s=triggered + self.post_seconds,
            day=day or datetime.fromtimestamp(wall, UTC).date().isoformat(),
            context=dict(_json_value(dict(context or {}))),
            _merge_enabled=merge_enabled,
        )
        for frame in self._ring:
            if start - 1e-9 <= frame.captured_monotonic_s <= triggered + 1e-9:
                self._append_event_frame_locked(event, frame)
        self._events[event_id] = event
        self._active[event_id] = event
        self._active_event_id = event_id
        return event

    def _encoder_loop(self) -> None:
        while True:
            job = self._input_jobs.get()
            try:
                if job is None:
                    return
                try:
                    encoded = self._jpeg_encoder(job.image, self.jpeg_quality)
                    if (
                        not isinstance(encoded, (bytes, bytearray, memoryview))
                        or not encoded
                    ):
                        raise RuntimeError("jpeg_encoder_returned_no_bytes")
                    frame = BufferedJpeg(
                        captured_monotonic_s=job.captured_monotonic_s,
                        captured_wall_time_s=job.captured_wall_time_s,
                        source_sequence=job.source_sequence,
                        data=bytes(encoded),
                        telemetry=job.telemetry,
                    )
                    with self._lock:
                        self._accept_encoded_locked(frame)
                except Exception:
                    with self._lock:
                        self._dropped_input_frames += 1
                        self._condition.notify_all()
            finally:
                with self._lock:
                    if job is not None:
                        self._finished_frame_jobs += 1
                    self._condition.notify_all()
                self._input_jobs.task_done()

    def _accept_encoded_locked(self, frame: BufferedJpeg) -> None:
        self._ring.append(frame)
        self._ring_bytes += len(frame.data)
        cutoff = frame.captured_monotonic_s - self.pre_seconds
        while self._ring and self._ring[0].captured_monotonic_s < cutoff - 1e-9:
            removed = self._ring.popleft()
            self._ring_bytes -= len(removed.data)
        for event in self._active.values():
            timestamp = frame.captured_monotonic_s
            # The post-window endpoint is deliberately half-open. A 4 fps
            # clip covering [-1, +1] therefore contains exactly eight frames,
            # while its declared time span remains two seconds.
            if (
                event.window_start_monotonic_s - 1e-9
                <= timestamp
                < event.deadline_monotonic_s - 1e-9
            ):
                self._append_event_frame_locked(event, frame)
        self._condition.notify_all()

    @staticmethod
    def _append_event_frame_locked(
        event: ReviewClipEvent, frame: BufferedJpeg
    ) -> None:
        key = (frame.captured_monotonic_s, frame.source_sequence)
        if key not in event._frame_keys:
            event._frame_keys.add(key)
            event._frames.append(frame)

    def _queue_expired_locked(self, now_s: float) -> int:
        count = 0
        for event in list(self._active.values()):
            if now_s + 1e-9 >= event.deadline_monotonic_s:
                self._queue_event_locked(event, truncated=False)
                count += 1
        return count

    def _queue_event_locked(
        self, event: ReviewClipEvent, *, truncated: bool
    ) -> None:
        if event.status != "CAPTURING":
            return
        self._active.pop(event.event_id, None)
        if self._active_event_id == event.event_id:
            remaining = list(self._active)
            self._active_event_id = remaining[-1] if remaining else None
        event.status = "WRITING"
        event.truncated = bool(truncated)
        self._write_jobs.put(event)
        self._condition.notify_all()

    def _writer_loop(self) -> None:
        while True:
            event = self._write_jobs.get()
            try:
                if event is None:
                    return
                try:
                    self._write_event(event)
                except Exception as error:
                    self._mark_failed(event, f"{type(error).__name__}: {error}"[:240])
                with self._lock:
                    self._completed_for_drain.append(event.event_id)
                    self._condition.notify_all()
            finally:
                self._write_jobs.task_done()

    def _event_paths(self, event: ReviewClipEvent) -> dict[str, Path]:
        directory = self.output_root / _safe(event.day) / event.station_id / "clips"
        directory.mkdir(parents=True, exist_ok=True)
        stem = _safe(event.event_id)
        return {
            "clip": directory / f"{stem}.mp4",
            "timeline": directory / f"{stem}.timeline.json",
            "thumbnail": directory / f"{stem}.thumbnail.jpg",
            "metadata": directory / f"{stem}.metadata.json",
        }

    def _write_event(self, event: ReviewClipEvent) -> None:
        paths = self._event_paths(event)
        event.metadata_path = paths["metadata"]
        frames = sorted(
            (
                frame
                for frame in event._frames
                if event.window_start_monotonic_s - 1e-9
                <= frame.captured_monotonic_s
                and frame.captured_monotonic_s
                < event.deadline_monotonic_s - 1e-9
            ),
            key=lambda item: (item.captured_monotonic_s, item.source_sequence),
        )
        if not frames:
            self._mark_failed(event, "event_contains_no_video_frames", paths=paths)
            return

        decoded_frames: list[tuple[BufferedJpeg, np.ndarray]] = []
        for frame in frames:
            array = np.frombuffer(frame.data, dtype=np.uint8)
            image = cv2.imdecode(array, cv2.IMREAD_COLOR)
            if image is not None and image.ndim == 3 and image.shape[2] == 3:
                decoded_frames.append((frame, image))
        if not decoded_frames:
            self._mark_failed(event, "event_contains_no_video_frames", paths=paths)
            return

        first_image = decoded_frames[0][1]
        if self.output_size is not None:
            width, height = self.output_size
        else:
            height, width = first_image.shape[:2]
            if width > self.max_width:
                scale = self.max_width / float(width)
                width = self.max_width
                height = max(2, int(round(height * scale)))
            width -= width % 2
            height -= height % 2

        temporary = paths["clip"].with_name(
            paths["clip"].stem + ".writing.mp4"
        )
        writer = cv2.VideoWriter(
            str(temporary),
            cv2.VideoWriter_fourcc(*"mp4v"),
            self.sample_fps,
            (width, height),
        )
        if not writer.isOpened():
            writer.release()
            self._mark_failed(
                event, "opencv_video_writer_open_failed", paths=paths
            )
            return
        try:
            for _, image in decoded_frames:
                if image.shape[1] != width or image.shape[0] != height:
                    interpolation = (
                        cv2.INTER_AREA if image.shape[1] >= width else cv2.INTER_LINEAR
                    )
                    image = cv2.resize(
                        image, (width, height), interpolation=interpolation
                    )
                writer.write(image)
        finally:
            writer.release()
        temporary.replace(paths["clip"])

        selected = [item[0] for item in decoded_frames]
        trigger_index = next(
            (
                index
                for index, frame in enumerate(selected)
                if frame.captured_monotonic_s
                >= event.triggered_monotonic_s - 1e-9
            ),
            len(selected) - 1,
        )
        trigger_index = max(0, trigger_index)
        thumbnail = decoded_frames[trigger_index][1]
        ok, thumbnail_bytes = cv2.imencode(
            ".jpg",
            thumbnail,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            raise RuntimeError("thumbnail_encoding_failed")
        paths["thumbnail"].write_bytes(thumbnail_bytes.tobytes())

        timestamps = [frame.captured_monotonic_s for frame in selected]
        event.frame_count = len(selected)
        event.duration_seconds = round(
            event.deadline_monotonic_s - event.window_start_monotonic_s, 6
        )
        event.actual_pre_seconds = round(
            max(0.0, event.triggered_monotonic_s - timestamps[0]), 6
        )
        event.actual_post_seconds = round(
            max(0.0, timestamps[-1] - event.triggered_monotonic_s), 6
        )
        event.gap_frame_count = sum(
            1
            for previous, current in zip(timestamps, timestamps[1:])
            if current - previous > self.max_frame_gap_s + 1e-9
        )
        event.trigger_frame_index = trigger_index
        event.clip_complete = not event.truncated and event.gap_frame_count == 0
        event.clip_path = paths["clip"]
        event.timeline_path = paths["timeline"]
        event.thumbnail_path = paths["thumbnail"]
        event.sha256 = hashlib.sha256(paths["clip"].read_bytes()).hexdigest()
        event.status = "READY"
        event.error = None

        timeline = {
            "schema_version": 1,
            "event_id": event.event_id,
            "station_id": event.station_id,
            "trigger_frame_index": trigger_index,
            "sample_fps": self.sample_fps,
            "frames": [
                {
                    "index": index,
                    "captured_monotonic_s": round(
                        frame.captured_monotonic_s, 9
                    ),
                    "captured_wall_time_s": round(frame.captured_wall_time_s, 6),
                    "relative_to_trigger_s": round(
                        frame.captured_monotonic_s
                        - event.triggered_monotonic_s,
                        6,
                    ),
                    "source_sequence": frame.source_sequence,
                    "telemetry": frame.telemetry,
                }
                for index, frame in enumerate(selected)
            ],
        }
        self._write_json_atomic(paths["timeline"], timeline)
        self._write_json_atomic(paths["metadata"], self._event_metadata(event))

    def _mark_failed(
        self,
        event: ReviewClipEvent,
        error: str,
        *,
        paths: dict[str, Path] | None = None,
    ) -> None:
        paths = paths or self._event_paths(event)
        event.status = "FAILED"
        event.error = str(error)[:240]
        event.clip_complete = False
        event.clip_path = None
        event.timeline_path = None
        event.thumbnail_path = None
        event.metadata_path = paths["metadata"]
        for key in ("clip", "timeline", "thumbnail"):
            try:
                paths[key].unlink(missing_ok=True)
            except OSError:
                pass
        self._write_json_atomic(paths["metadata"], self._event_metadata(event))

    def _event_metadata(self, event: ReviewClipEvent) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": event.event_id,
            "station_id": event.station_id,
            "state": event.state,
            "score": event.score,
            "reason": event.reason,
            "status": event.status,
            "trigger_count": event.trigger_count,
            "merged_trigger_count": event.merged_trigger_count,
            "triggered_monotonic_s": event.triggered_monotonic_s,
            "triggered_wall_time_s": event.triggered_wall_time_s,
            "last_triggered_monotonic_s": event.last_triggered_monotonic_s,
            "pre_seconds": self.pre_seconds,
            "post_seconds": self.post_seconds,
            "sample_fps": self.sample_fps,
            "frame_count": event.frame_count,
            "duration_seconds": event.duration_seconds,
            "actual_pre_seconds": event.actual_pre_seconds,
            "actual_post_seconds": event.actual_post_seconds,
            "gap_frame_count": event.gap_frame_count,
            "trigger_frame_index": event.trigger_frame_index,
            "truncated": event.truncated,
            "clip_complete": event.clip_complete,
            "clip_path": str(event.clip_path) if event.clip_path else None,
            "timeline_path": str(event.timeline_path)
            if event.timeline_path
            else None,
            "thumbnail_path": str(event.thumbnail_path)
            if event.thumbnail_path
            else None,
            "sha256": event.sha256,
            "error": event.error,
            "context": event.context,
            "face_blurred": True,
            "contains_identity": False,
        }

    def _compat_metadata(self, event: ReviewClipEvent | None) -> dict[str, Any]:
        if event is None:
            status = "PENDING"
        elif event.status == "CAPTURING":
            status = "RECORDING"
        elif event.status == "WRITING":
            status = "PENDING"
        else:
            status = event.status
        result: dict[str, Any] = {
            "status": status,
            "path": str(event.clip_path)
            if event is not None and event.clip_path
            else None,
            "pre_roll_s": self.pre_seconds,
            "post_roll_s": self.post_seconds,
            "fps": self.sample_fps,
            "frame_count": 0
            if event is None
            else int(event.frame_count or len(event._frames)),
            "face_blurred": True,
            "contains_identity": False,
        }
        if event is not None and event.clip_path is not None:
            capture = cv2.VideoCapture(str(event.clip_path))
            try:
                result["width"] = max(
                    0, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
                )
                result["height"] = max(
                    0, int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                )
            finally:
                capture.release()
            result["trigger_frame_index"] = event.trigger_frame_index
        if event is not None and event.error:
            result["error"] = event.error
        return result

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _put_sentinel(target: queue.Queue[Any]) -> None:
        # close() is not part of the realtime path; bounded waiting here is
        # preferable to abandoning an accepted JPEG job.
        while True:
            try:
                target.put(None, timeout=0.1)
                return
            except queue.Full:
                continue
