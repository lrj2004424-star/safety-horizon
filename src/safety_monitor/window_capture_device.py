"""Read a named macOS app window through native streaming or composited capture."""

from __future__ import annotations

import select
import struct
import subprocess
import time
import json
import os
from pathlib import Path

import cv2
import numpy as np

from .camera_device import CameraRead


def select_fallback_window(
    windows: object,
    *,
    title_contains: str | None = None,
) -> dict[str, object] | None:
    """Select an EZVIZ window and never recurse into TouchDesigner."""
    if not isinstance(windows, list):
        return None
    required = (title_contains or "").strip().casefold()
    candidates: list[dict[str, object]] = []
    for item in windows:
        if not isinstance(item, dict):
            continue
        owner = str(item.get("owner", item.get("app_name", "")))
        title = str(item.get("title", ""))
        bundle = str(item.get("bundle_id", ""))
        identity = " ".join((owner, title, bundle)).casefold()
        if "touchdesigner" in owner.casefold() or "touchdesigner" in bundle.casefold():
            continue
        if required:
            if required not in identity:
                continue
        elif not any(token in identity for token in ("萤石云视频", "ezviz", "videogo")):
            continue
        try:
            width = float(item.get("width", 0))
            height = float(item.get("height", 0))
            int(item["window_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if width < 160 or height < 120:
            continue
        candidates.append(item)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: float(item.get("width", 0)) * float(item.get("height", 0)),
    )


class WindowCaptureDevice:
    """CameraDevice-compatible live source backed by a single macOS window."""

    def __init__(
        self,
        helper_path: str | Path,
        *,
        bundle_id: str,
        title_contains: str | None = None,
        max_width: int = 1280,
        max_height: int = 960,
        fps: int = 12,
        crop_normalized: tuple[float, float, float, float] | None = None,
        output_aspect_ratio: float | None = None,
        startup_timeout_s: float = 45.0,
        prefer_composited_fallback: bool = False,
    ) -> None:
        if max_width < 1 or max_height < 1 or not 1 <= fps <= 30:
            raise ValueError("Window capture dimensions must be positive and fps must be 1..30")
        if crop_normalized is not None:
            x0, y0, x1, y1 = crop_normalized
            if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
                raise ValueError("Window crop must be normalized x0,y0,x1,y1 values")
        if output_aspect_ratio is not None and output_aspect_ratio <= 0:
            raise ValueError("Window output aspect ratio must be positive")
        helper = Path(helper_path).expanduser().resolve()
        if not helper.is_file():
            raise FileNotFoundError(f"Window capture helper is missing: {helper}")
        self._process: subprocess.Popen[bytes] | None = None
        self._sequence = 0
        self._reconnect_count = 0
        self._last_error = ""
        self._fallback_capture = False
        self._fallback_window_id: int | None = None
        self._title_contains = title_contains
        # CoreGraphics lister is available as an opt-in diagnostics fallback.
        # The signed ScreenCaptureKit helper remains the normal path.
        native_lister = helper.parent / "ezviz_window_list_c"
        use_native_lister = os.environ.get("EZVIZ_USE_COREGRAPHICS_LISTER") == "1"
        self._fallback_lister = (
            native_lister
            if use_native_lister and native_lister.is_file()
            else helper.parent / "ezviz_window_list"
        )
        self._fallback_frame_path = Path("/private/tmp") / f"ezviz-window-fallback-{os.getpid()}.png"
        if prefer_composited_fallback:
            if not self._fallback_lister.is_file():
                raise FileNotFoundError(f"Window lister is missing: {self._fallback_lister}")
            # Tencent's Android-container window reports a landscape backing
            # store even while its visible macOS window is portrait. Capture
            # the composited named window so the camera region has stable,
            # crop-ready geometry and the surrounding app UI can be excluded.
            self._fallback_capture = True
            self.raw_width, self.raw_height, self.fps = max_width, max_height, fps
            self.width, self.height = max_width, max_height
        else:
            command = [
                str(helper),
                "--bundle-id",
                bundle_id,
                "--max-width",
                str(max_width),
                "--max-height",
                str(max_height),
                "--fps",
                str(fps),
            ]
            if title_contains:
                command.extend(["--title-contains", title_contains])
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            header = self._read_exact(16, startup_timeout_s)
            if len(header) != 16 or header[:4] != b"EZV1":
                self._last_error = self._read_stderr()
                self.release()
                message = self._last_error or "window_capture_helper_no_header"
                if self._fallback_lister.is_file():
                    # Some macOS setups grant screen capture to the parent app but
                    # reject or intermittently disconnect the ScreenCaptureKit
                    # child helper.  The native `screencapture -l <window-id>` path
                    # remains a local, strictly selected named-window fallback.
                    self._fallback_capture = True
                    self._process = None
                    self.raw_width, self.raw_height, self.fps = max_width, max_height, fps
                    self.width, self.height = max_width, max_height
                else:
                    raise RuntimeError(message)
            else:
                self.raw_width, self.raw_height, self.fps = struct.unpack("<III", header[4:])
        self._frame_bytes = self.raw_width * self.raw_height * 4
        self._crop_pixels: tuple[int, int, int, int] | None = None
        self.width, self.height = self.raw_width, self.raw_height
        if crop_normalized is not None:
            x0, y0, x1, y1 = crop_normalized
            left = max(0, min(self.raw_width - 1, int(round(x0 * self.raw_width))))
            top = max(0, min(self.raw_height - 1, int(round(y0 * self.raw_height))))
            right = max(left + 1, min(self.raw_width, int(round(x1 * self.raw_width))))
            bottom = max(top + 1, min(self.raw_height, int(round(y1 * self.raw_height))))
            self._crop_pixels = (left, top, right, bottom)
            crop_width, crop_height = right - left, bottom - top
            if output_aspect_ratio is None:
                scale = min(max_width / crop_width, max_height / crop_height)
                self.width = max(2, int(round(crop_width * scale)) // 2 * 2)
                self.height = max(2, int(round(crop_height * scale)) // 2 * 2)
            else:
                target_width = min(max_width, int(round(max_height * output_aspect_ratio)))
                target_height = int(round(target_width / output_aspect_ratio))
                self.width = max(2, target_width // 2 * 2)
                self.height = max(2, target_height // 2 * 2)

    def is_opened(self) -> bool:
        if self._fallback_capture:
            return self._fallback_lister.is_file()
        return self._process.poll() is None and self._process.stdout is not None

    def read_observation(self) -> CameraRead:
        if self._fallback_capture:
            return self._read_fallback_observation()
        if not self.is_opened():
            return self._failure("window_capture_process_stopped")
        raw = self._read_exact(self._frame_bytes, 5.0)
        captured = time.monotonic()
        if len(raw) != self._frame_bytes:
            self._last_error = self._read_stderr()
            return self._failure(self._last_error or "window_capture_frame_timeout")
        bgra = np.frombuffer(raw, dtype=np.uint8).reshape(self.raw_height, self.raw_width, 4)
        frame = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        if self._crop_pixels is not None:
            left, top, right, bottom = self._crop_pixels
            frame = frame[top:bottom, left:right]
            if frame.shape[1] != self.width or frame.shape[0] != self.height:
                frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_CUBIC)
        sequence = self._sequence
        self._sequence += 1
        return CameraRead(
            True,
            frame,
            captured,
            sequence,
            self._reconnect_count,
            "window_frame_ok",
        )

    def _read_fallback_observation(self) -> CameraRead:
        """Read a named window through macOS screencapture as a TCC fallback."""
        try:
            if self._fallback_window_id is None:
                listed = subprocess.run(
                    [str(self._fallback_lister)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=2.0,
                )
                windows = json.loads(listed.stdout.decode("utf-8")) if listed.returncode == 0 else []
                target = select_fallback_window(
                    windows,
                    title_contains=self._title_contains,
                )
                if target is None:
                    return self._failure("fallback_target_window_not_found")
                self._fallback_window_id = int(target["window_id"])
            shot = subprocess.run(
                [
                    "/usr/sbin/screencapture",
                    "-x",
                    "-l",
                    str(self._fallback_window_id),
                    str(self._fallback_frame_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=3.0,
            )
            if shot.returncode != 0:
                detail = shot.stderr.decode("utf-8", errors="replace").strip()
                # The application may have been reopened and received a new
                # window id.  Re-resolve it on the next retry.
                self._fallback_window_id = None
                return self._failure(detail or "fallback_window_capture_failed")
            frame = cv2.imread(str(self._fallback_frame_path), cv2.IMREAD_COLOR)
            if frame is None or frame.size == 0:
                return self._failure("fallback_window_image_invalid")
            self.raw_width, self.raw_height = frame.shape[1], frame.shape[0]
            self.width, self.height = self.raw_width, self.raw_height
            sequence = self._sequence
            self._sequence += 1
            return CameraRead(
                True,
                frame,
                time.monotonic(),
                sequence,
                self._reconnect_count,
                "window_frame_ok:screencapture_fallback",
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            return self._failure(f"fallback_window_capture_error:{type(error).__name__}")

    def read(self) -> tuple[bool, np.ndarray | None]:
        observation = self.read_observation()
        return observation.ok, observation.frame

    def get(self, property_id: int) -> float:
        if property_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.width)
        if property_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.height)
        if property_id == cv2.CAP_PROP_FPS:
            return float(self.fps)
        if property_id == cv2.CAP_PROP_POS_FRAMES:
            return float(self._sequence)
        return 0.0

    def set(self, property_id: int, value: float) -> bool:
        return False

    def release(self) -> None:
        process = getattr(self, "_process", None)
        if process is None:
            self._fallback_frame_path.unlink(missing_ok=True)
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        self._fallback_frame_path.unlink(missing_ok=True)

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def last_error(self) -> str:
        return self._last_error

    def _failure(self, reason: str) -> CameraRead:
        return CameraRead(
            False,
            None,
            time.monotonic(),
            self._sequence,
            self._reconnect_count,
            reason,
        )

    def _read_exact(self, size: int, timeout_s: float) -> bytes:
        if self._process.stdout is None:
            return b""
        deadline = time.monotonic() + max(0.1, timeout_s)
        chunks: list[bytes] = []
        remaining = size
        descriptor = self._process.stdout.fileno()
        while remaining > 0 and time.monotonic() < deadline:
            timeout = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([descriptor], [], [], min(timeout, 0.25))
            if not readable:
                continue
            chunk = self._process.stdout.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_stderr(self) -> str:
        if self._process.stderr is None:
            return ""
        descriptor = self._process.stderr.fileno()
        readable, _, _ = select.select([descriptor], [], [], 0.05)
        if not readable:
            return ""
        return self._process.stderr.read(8192).decode("utf-8", errors="replace").strip()

    def __enter__(self) -> "WindowCaptureDevice":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
