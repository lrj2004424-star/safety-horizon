#!/usr/bin/env python3
"""Two independent whole-person fatigue observers from one visible EZVIZ window."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from safety_monitor.capture_manager import CaptureManager
from safety_monitor.frame_activity import FrameActivityGuard
from safety_monitor.config import StationConfig, load_config
from safety_monitor.fatigue_engine import FatigueAssessment, FatigueRiskEngine, FatigueState
from safety_monitor.fatigue_status import (
    MultiStationFatigueStatusWriter,
    aggregate_fatigue_assessments,
)
from safety_monitor.fatigue_visualization import (
    CANDIDATE_COLOR,
    COLORS,
    STATE_LABELS,
    draw_station_fatigue_overlay,
    draw_station_pose_overlay,
)
from safety_monitor.input_router import VisionInputRouter
from safety_monitor.pose_tracker import (
    PoseObservation,
    PosePoint,
    PoseTracker,
    build_pose_observation,
)
from safety_monitor.privacy import blur_face_from_pose
from safety_monitor.wide_station import (
    WideCameraManifest,
    WideStationSpec,
    crop_frame,
    load_wide_manifest,
)
from safety_monitor.window_capture_device import WindowCaptureDevice


PROJECT_ROOT = Path(__file__).resolve().parent
EZVIZ_BUNDLE_ID = "com.tencent.yybmac.app.com.videogo"
CG_WINDOW_HELPER = PROJECT_ROOT / "macos_window_capture" / "ezviz_window_capture_cg"
SWIFT_WINDOW_HELPER = PROJECT_ROOT / "macos_window_capture" / "ezviz_window_capture"
DEFAULT_WINDOW_HELPER = CG_WINDOW_HELPER if CG_WINDOW_HELPER.is_file() else SWIFT_WINDOW_HELPER
DEFAULT_INPUT_MODE_PATH = PROJECT_ROOT / "runtime" / "vision-input-mode.json"
DEFAULT_ACTIVE_THRESHOLDS_PATH = PROJECT_ROOT / "config" / "fatigue-thresholds.active.json"
STATION_LABELS = {
    # The current TouchDesigner deployment intentionally watches only this
    # left-side work area.  Keep the stable id for the backed-up dual version.
    "upper_station": "STATION A - LEFT",
    "lower_station": "STATION B - LOWER",
}


@dataclass(frozen=True)
class StationResult:
    station_id: str
    label: str
    annotated: np.ndarray
    assessment: FatigueAssessment
    presentation: np.ndarray | None = None
    input_frame: np.ndarray | None = None
    pose_candidates: tuple[PoseObservation, ...] = ()


class StationFatigueRuntime:
    """One isolated pose tracker, personal baseline and state machine per crop."""

    def __init__(
        self,
        spec: WideStationSpec,
        config: StationConfig,
        *,
        runtime_index: int,
        baseline_seconds: float,
        trend_seconds: float,
        high_risk_seconds: float,
        warning_score_min: int = 45,
        danger_score_min: int = 70,
    ) -> None:
        self.spec = spec
        self.label = STATION_LABELS.get(spec.station_id, spec.station_id.upper())
        self.runtime_index = runtime_index
        model = PROJECT_ROOT / "models" / "pose_landmarker_full.task"
        if not model.is_file():
            configured = Path(config.pose.model_path).expanduser()
            model = configured if configured.is_absolute() else PROJECT_ROOT / configured
        self.pose_tracker = PoseTracker(
            model,
            num_poses=1,
            # The EZVIZ desktop stream is strongly compressed and the worker
            # occupies only part of the wide frame.  Conservative 0.40 gates
            # rejected clearly visible people.  Keep filtering in the
            # temporal risk engine, but let the landmarker return candidates.
            min_detection_confidence=min(0.18, config.pose.min_detection_confidence),
            min_presence_confidence=min(0.18, config.pose.min_presence_confidence),
            min_tracking_confidence=min(0.18, config.pose.min_tracking_confidence),
            min_landmark_visibility=min(0.12, config.pose.min_landmark_visibility),
            # Re-run the detector for each sampled frame.  VIDEO mode can
            # remain locked to a lost track in this compressed fixed-camera
            # view; IMAGE mode is steadier for one small, distant worker.
            image_mode=True,
        )
        lower_station = spec.station_id == "lower_station"
        self.engine = FatigueRiskEngine(
            baseline_seconds=baseline_seconds,
            trend_seconds=trend_seconds,
            high_risk_seconds=high_risk_seconds,
            warning_score_min=warning_score_min,
            danger_score_min=danger_score_min,
            max_people=1,
            min_body_visibility=min(0.20, config.pose.min_landmark_visibility),
            min_torso_length=0.08 if lower_station else 0.045,
            min_body_coverage=0.60 if lower_station else 0.34,
            min_body_span=0.38 if lower_station else 0.22,
            min_track_confirmations=3 if lower_station else 2,
            require_lower_body_order=lower_station,
        )
        self._last_assessment: FatigueAssessment | None = None
        self._last_pose_candidates: tuple[PoseObservation, ...] = ()
        self._last_pose_candidate_s = float("-inf")

    def close(self) -> None:
        self.pose_tracker.close()

    def fault(self, reason: str) -> FatigueAssessment:
        result = self.engine.fault(reason)
        self._last_assessment = result
        return result

    def process(
        self,
        source_frame: np.ndarray,
        *,
        frame_index: int,
        timestamp_s: float,
    ) -> StationResult:
        crop = crop_frame(source_frame, self.spec.crop)
        interval = max(1, self.spec.pose_interval_frames)
        detection_due = (
            self._last_assessment is None
            or (frame_index + self.runtime_index) % interval == 0
        )
        if detection_due:
            # Upscale the small single-workstation crop before inference.  The
            # overlay still uses the original frame, so this improves pose
            # acquisition without increasing TouchDesigner output bandwidth.
            # The right quarter of this calibrated crop is storage shelving,
            # not a workstation.  Searching it at a very permissive threshold
            # can produce a geometrically plausible but visibly false pose.
            # Keep it in the operator's video evidence, while limiting pose
            # acquisition to the actual left work belt and remapping every
            # landmark back to the full displayed crop.
            focus_right = max(1, int(round(crop.shape[1] * 0.74)))
            pose_crop = crop[:, :focus_right]
            inference = _resize_for_pose(pose_crop, target_long_side=960)
            raw_observations = self.pose_tracker.detect_observations(
                inference, int(timestamp_s * 1000)
            )
            observations = [
                _scale_pose_x(observation, focus_right / crop.shape[1])
                for observation in raw_observations
            ]
            if observations:
                self._last_pose_candidates = tuple(observations)
                self._last_pose_candidate_s = timestamp_s
            elif timestamp_s - self._last_pose_candidate_s > 2.0:
                self._last_pose_candidates = ()
            self._last_assessment = self.engine.update(observations, timestamp_s)
        assert self._last_assessment is not None
        assessment = self._last_assessment
        pose_candidates = self._last_pose_candidates
        private_crop = crop.copy()
        for person in assessment.persons:
            private_crop = blur_face_from_pose(private_crop, person.pose)
        if not assessment.persons:
            for pose in pose_candidates:
                private_crop = blur_face_from_pose(private_crop, pose)
        annotated = draw_station_fatigue_overlay(
            private_crop,
            assessment,
            self.label,
            pose_candidates,
        )
        presentation = draw_station_pose_overlay(
            private_crop,
            assessment,
            pose_candidates,
        )
        return StationResult(
            self.spec.station_id,
            self.label,
            annotated,
            assessment,
            presentation,
            private_crop,
            pose_candidates,
        )


def _scale_pose_x(observation: PoseObservation, x_scale: float) -> PoseObservation:
    """Map landmarks from a left-side inference crop to the displayed crop."""
    points = tuple(
        PosePoint(
            x=float(point.x) * float(x_scale),
            y=float(point.y),
            visibility=float(point.visibility),
            presence=float(point.presence),
        )
        for point in observation.landmarks
    )
    return build_pose_observation(points, min_visibility=0.12)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "config" / "ezviz-fatigue-multistation.json"))
    parser.add_argument("--window-app", default=EZVIZ_BUNDLE_ID)
    parser.add_argument("--window-title")
    parser.add_argument('--source-config')
    parser.add_argument("--window-helper", default=str(DEFAULT_WINDOW_HELPER))
    parser.add_argument("--window-crop", default="auto")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--captures-dir", default=str(PROJECT_ROOT / "artifacts" / "ezviz-window-captures"))
    parser.add_argument("--status-json")
    parser.add_argument("--baseline-seconds", type=float, default=20.0)
    parser.add_argument("--trend-seconds", type=float, default=20.0)
    parser.add_argument("--high-risk-seconds", type=float, default=45.0)
    parser.add_argument(
        "--input-mode-json",
        default=str(DEFAULT_INPUT_MODE_PATH),
        help="Atomic LIVE/TEST routing state written by the safety-officer terminal.",
    )
    parser.add_argument(
        "--active-thresholds-json",
        default=str(DEFAULT_ACTIVE_THRESHOLDS_PATH),
        help="Explicitly approved score-band configuration; missing means 45/70 defaults.",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--dashboard-output",
        help=(
            "Optional JPEG path for an external dashboard such as TouchDesigner. "
            "The image is written atomically and contains only the already-blurred view."
        ),
    )
    parser.add_argument(
        "--dashboard-output-fps",
        type=float,
        default=20.0,
        help="Maximum JPEG update rate for --dashboard-output (default: 20)",
    )
    parser.add_argument(
        "--source-preview-output",
        help=(
            "Optional local-only JPEG path for the live input frame before OpenCV overlays. "
            "It is continuously overwritten and is intended for TouchDesigner workflow preview."
        ),
    )
    parser.add_argument(
        "--station-input-output",
        help="Optional JPEG of the selected left work-area crop before pose overlays.",
    )
    parser.add_argument(
        "--vision-output",
        help="Optional JPEG of the OpenCV/MediaPipe annotated left work area.",
    )
    parser.add_argument(
        "--risk-card-output",
        help="Optional JPEG risk-decision card for the TouchDesigner workflow.",
    )
    parser.add_argument("--max-frames", type=int, default=0)
    return parser.parse_args(argv)


def parse_window_crop(raw: str) -> str | tuple[float, float, float, float]:
    mode = raw.strip().lower()
    if mode in {"auto", "full", "none"}:
        return "full" if mode == "none" else mode
    values = tuple(float(value.strip()) for value in raw.split(","))
    if len(values) != 4:
        raise ValueError("window crop must be x0,y0,x1,y1")
    x0, y0, x1, y1 = values
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("window crop values must be normalized")
    return x0, y0, x1, y1


def load_active_score_thresholds(path: str | Path) -> tuple[int, int, str]:
    """Load only an explicitly approved, internally consistent threshold file."""
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        thresholds = payload.get("thresholds") if isinstance(payload, dict) else None
        warning = int(thresholds.get("warning_min")) if isinstance(thresholds, dict) else 45
        danger = int(thresholds.get("danger_min")) if isinstance(thresholds, dict) else 70
        if (
            payload.get("status") != "ACTIVE"
            or payload.get("explicit_approval") is not True
            or payload.get("machine_control_enabled") is not False
            or not 1 <= warning < danger <= 100
        ):
            raise ValueError("active threshold contract is invalid")
        version = str(payload.get("version", "review-approved-v1"))[:80]
        return warning, danger, version
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return 45, 70, "fixed-default-v1"


def extract_video_frame(
    frame: np.ndarray,
    crop_mode: str | tuple[float, float, float, float],
) -> np.ndarray:
    """Extract the player from portrait mobile UI or keep a landscape player."""
    height, width = frame.shape[:2]
    if crop_mode == "auto":
        # The portrait Tencent-container player has title/navigation overlays
        # above the camera and a playback progress line below it.  Crop a
        # centered 16:9 safe area *inside* the video pixels so neither the app
        # chrome nor the camera timestamp/back-arrow band enters OpenCV/TD.
        crop = (0.049, 0.065, 0.951, 0.338) if height / width > 1.25 else (0.0, 0.0, 1.0, 1.0)
    elif crop_mode == "full":
        crop = (0.0, 0.0, 1.0, 1.0)
    else:
        crop = crop_mode
    x0, y0, x1, y1 = crop
    left = max(0, min(width - 1, int(round(x0 * width))))
    top = max(0, min(height - 1, int(round(y0 * height))))
    right = max(left + 1, min(width, int(round(x1 * width))))
    bottom = max(top + 1, min(height, int(round(y1 * height))))
    player = frame[top:bottom, left:right]
    return cv2.resize(player, (1280, 720), interpolation=cv2.INTER_CUBIC)


def is_usable_video_frame(frame: np.ndarray) -> bool:
    """Reject privacy-protected lock-screen frames and blank player surfaces."""
    if frame.size == 0:
        return False
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    mean, standard_deviation = cv2.meanStdDev(gray)
    level = float(mean[0, 0])
    variation = float(standard_deviation[0, 0])
    if variation < 3.0:
        # Loading surfaces are often light grey, not pure white/black.
        return False
    # Known EZVIZ home-page signature: a large cyan header and a nearly
    # full-width white separator above device thumbnails. Fail closed rather
    # than infer an employee skeleton from a thumbnail/UI icon. This is a
    # conservative rejection heuristic, not proof that all other frames are live.
    small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    cyan = (hsv[:, :, 0] >= 85) & (hsv[:, :, 0] <= 105) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 170)
    white = (small.min(axis=2) > 185) & ((small.max(axis=2).astype(np.int16) - small.min(axis=2)) < 20)
    if float(cyan.mean()) > 0.25 and np.any(white.mean(axis=1) > 0.85):
        return False
    return True


def _resize_for_pose(frame: np.ndarray, target_long_side: int = 720) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = min(2.0, max(1.0, target_long_side / max(width, height)))
    target_width = max(2, int(round(width * scale)) // 2 * 2)
    target_height = max(2, int(round(height * scale)) // 2 * 2)
    if (target_width, target_height) == (width, height):
        return frame
    return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_CUBIC)


def _letterbox(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    canvas = np.full((height, width, 3), (14, 17, 22), dtype=np.uint8)
    scale = min(width / frame.shape[1], height / frame.shape[0])
    target_width = max(1, int(round(frame.shape[1] * scale)))
    target_height = max(1, int(round(frame.shape[0] * scale)))
    resized = cv2.resize(
        frame,
        (target_width, target_height),
        interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
    )
    x0 = (width - target_width) // 2
    y0 = (height - target_height) // 2
    canvas[y0 : y0 + target_height, x0 : x0 + target_width] = resized
    return canvas


def write_dashboard_jpeg(path: Path, dashboard: np.ndarray) -> None:
    """Atomically expose the privacy-filtered dashboard to another local UI.

    TouchDesigner reloads a still image while this monitor owns capture and pose
    inference.  Encoding to memory first avoids a consumer ever reading a
    half-written image.
    """
    ok, encoded = cv2.imencode(
        ".jpg", dashboard, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
    )
    if not ok:
        raise RuntimeError("dashboard_jpeg_encode_failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded.tobytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _draw_metric_bar(
    canvas: np.ndarray,
    *,
    x: int,
    y: int,
    width: int,
    label: str,
    value: str,
    level: float,
    color: tuple[int, int, int],
) -> None:
    """Render one compact, comparable whole-person signal."""
    bounded = min(1.0, max(0.0, float(level)))
    cv2.putText(canvas, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (158, 170, 184), 1, cv2.LINE_AA)
    value_size = cv2.getTextSize(value, cv2.FONT_HERSHEY_DUPLEX, 0.48, 1)[0]
    cv2.putText(canvas, value, (x + width - value_size[0], y), cv2.FONT_HERSHEY_DUPLEX, 0.48, (236, 240, 244), 1, cv2.LINE_AA)
    bar_y = y + 11
    cv2.rectangle(canvas, (x, bar_y), (x + width, bar_y + 6), (37, 46, 57), -1)
    if bounded > 0:
        cv2.rectangle(canvas, (x, bar_y), (x + int(round(width * bounded)), bar_y + 6), color, -1)


def _draw_risk_sparkline(
    canvas: np.ndarray,
    values: list[int],
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple[int, int, int],
) -> None:
    cv2.rectangle(canvas, (x, y), (x + width, y + height), (29, 36, 45), -1)
    for threshold in (45, 70):
        line_y = y + height - int(round(height * threshold / 100.0))
        cv2.line(canvas, (x, line_y), (x + width, line_y), (55, 64, 75), 1, cv2.LINE_AA)
    if len(values) < 2:
        return
    recent = values[-90:]
    points = np.asarray(
        [
            (
                x + int(round(index * width / max(1, len(recent) - 1))),
                y + height - int(round(min(100, max(0, value)) * height / 100.0)),
            )
            for index, value in enumerate(recent)
        ],
        dtype=np.int32,
    )
    cv2.polylines(canvas, [points], False, color, 2, cv2.LINE_AA)


def build_station_input_analysis(
    source_frame: np.ndarray,
    station: WideStationSpec,
    result: StationResult,
) -> np.ndarray:
    """Build a wide source-to-ROI analysis page without anonymous side bars."""
    canvas = np.full((720, 1280, 3), (8, 12, 17), dtype=np.uint8)
    accent = (226, 167, 54)
    cv2.putText(canvas, "01  CAMERA INPUT / WORK AREA", (24, 38), cv2.FONT_HERSHEY_DUPLEX, 0.78, (242, 245, 248), 2, cv2.LINE_AA)
    cv2.putText(canvas, "VISIBLE EZVIZ PLAYER  >  LEFT-STATION ROI", (24, 63), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (150, 164, 180), 1, cv2.LINE_AA)

    # Show the full camera and dim everything outside the active work area.
    source_left, source_top, source_width, source_height = 34, 96, 750, 422
    full = cv2.resize(source_frame, (source_width, source_height), interpolation=cv2.INTER_AREA)
    focused = np.clip(full.astype(np.float32) * 0.42, 0, 255).astype(np.uint8)
    x0, y0, x1, y1 = station.crop
    roi_left = max(0, min(source_width - 1, int(round(x0 * source_width))))
    roi_top = max(0, min(source_height - 1, int(round(y0 * source_height))))
    roi_right = max(roi_left + 1, min(source_width, int(round(x1 * source_width))))
    roi_bottom = max(roi_top + 1, min(source_height, int(round(y1 * source_height))))
    focused[roi_top:roi_bottom, roi_left:roi_right] = full[roi_top:roi_bottom, roi_left:roi_right]
    canvas[source_top : source_top + source_height, source_left : source_left + source_width] = focused
    cv2.rectangle(canvas, (source_left - 10, source_top - 10), (source_left + source_width + 10, source_top + source_height + 10), (25, 32, 40), 2)
    cv2.rectangle(
        canvas,
        (source_left + roi_left, source_top + roi_top),
        (source_left + roi_right, source_top + roi_bottom),
        accent,
        4,
        cv2.LINE_AA,
    )
    cv2.putText(canvas, "ACTIVE DETECTION AREA", (source_left + roi_left + 12, max(source_top + 25, source_top + roi_top - 10)), cv2.FONT_HERSHEY_DUPLEX, 0.46, accent, 1, cv2.LINE_AA)

    # Use the former empty lower area for a readable processing route.
    route_y = 572
    steps = ((34, "PLAYER", "1280 x 720"), (282, "LEFT ROI", "POSE INPUT"), (530, "ANALYSIS", "WHOLE PERSON"))
    for x, title, detail in steps:
        cv2.rectangle(canvas, (x, route_y), (x + 202, route_y + 82), (17, 23, 30), -1)
        cv2.rectangle(canvas, (x, route_y), (x + 5, route_y + 82), accent, -1)
        cv2.putText(canvas, title, (x + 18, route_y + 32), cv2.FONT_HERSHEY_DUPLEX, 0.52, (235, 239, 243), 1, cv2.LINE_AA)
        cv2.putText(canvas, detail, (x + 18, route_y + 59), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (150, 164, 180), 1, cv2.LINE_AA)
    for x in (242, 490):
        cv2.arrowedLine(canvas, (x, route_y + 41), (x + 30, route_y + 41), accent, 2, cv2.LINE_AA, tipLength=0.28)

    # Right card displays the exact crop at its native visual proportion.
    panel_left, panel_top, panel_width, panel_height = 814, 82, 442, 586
    cv2.rectangle(canvas, (panel_left, panel_top), (panel_left + panel_width, panel_top + panel_height), (17, 23, 30), -1)
    cv2.putText(canvas, "LEFT WORK AREA / LIVE ZOOM", (834, 116), cv2.FONT_HERSHEY_DUPLEX, 0.54, accent, 1, cv2.LINE_AA)
    station_frame = result.input_frame
    if station_frame is None:
        station_frame = crop_frame(source_frame, station.crop)
    zoom_width = 402
    zoom_height = max(1, int(round(zoom_width * station_frame.shape[0] / station_frame.shape[1])))
    zoom_height = min(356, zoom_height)
    zoom = cv2.resize(station_frame, (zoom_width, zoom_height), interpolation=cv2.INTER_AREA)
    zoom_top = 138
    canvas[zoom_top : zoom_top + zoom_height, 834 : 834 + zoom_width] = zoom
    cv2.rectangle(canvas, (834, zoom_top), (1236, zoom_top + zoom_height), accent, 2)

    roi_pixels = crop_frame(source_frame, station.crop)
    coverage = (x1 - x0) * (y1 - y0) * 100.0
    info = (
        ("SOURCE", f"{source_frame.shape[1]} x {source_frame.shape[0]}"),
        ("ROI", f"{roi_pixels.shape[1]} x {roi_pixels.shape[0]}"),
        ("FRAME COVERAGE", f"{coverage:4.1f}%"),
        ("POSE CANDIDATES", str(len(result.pose_candidates))),
    )
    info_y = 526
    for index, (label, value) in enumerate(info):
        y = info_y + index * 30
        cv2.putText(canvas, label, (834, y), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (145, 160, 176), 1, cv2.LINE_AA)
        value_width = cv2.getTextSize(value, cv2.FONT_HERSHEY_DUPLEX, 0.44, 1)[0][0]
        cv2.putText(canvas, value, (1236 - value_width, y), cv2.FONT_HERSHEY_DUPLEX, 0.44, (234, 239, 244), 1, cv2.LINE_AA)
    cv2.putText(canvas, "LOCAL SCREEN CAPTURE  /  NO NVR CREDENTIALS", (24, 704), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (126, 138, 152), 1, cv2.LINE_AA)
    return canvas


def build_pose_signal_analysis(result: StationResult) -> np.ndarray:
    """Build a wide pose-and-signal page with data beside the live body view."""
    canvas = np.full((720, 1280, 3), (8, 12, 17), dtype=np.uint8)
    assessment = result.assessment
    color = COLORS[assessment.state]
    cv2.putText(canvas, "02  OPENCV POSE / LIVE SIGNALS", (24, 38), cv2.FONT_HERSHEY_DUPLEX, 0.78, (242, 245, 248), 2, cv2.LINE_AA)
    cv2.putText(canvas, "ANONYMOUS WHOLE-PERSON LANDMARKS + TEMPORAL FEATURES", (24, 63), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (150, 164, 180), 1, cv2.LINE_AA)
    state_label = STATE_LABELS[assessment.state]
    state_width = cv2.getTextSize(state_label, cv2.FONT_HERSHEY_DUPLEX, 0.58, 2)[0][0]
    pill_left = 1238 - state_width
    cv2.rectangle(canvas, (pill_left, 17), (1256, 57), (23, 29, 36), -1)
    cv2.rectangle(canvas, (pill_left, 17), (pill_left + 6, 57), color, -1)
    cv2.putText(canvas, state_label, (pill_left + 13, 44), cv2.FONT_HERSHEY_DUPLEX, 0.58, color, 2, cv2.LINE_AA)

    # Match the card to the station aspect instead of centering it in a wide TOP.
    image_left, image_top, image_width, image_height = 24, 82, 690, 604
    cv2.rectangle(canvas, (image_left, image_top), (image_left + image_width, image_top + image_height), (17, 23, 30), -1)
    presentation = result.presentation if result.presentation is not None else result.annotated
    inner_height = image_height - 20
    inner_width = max(1, int(round(inner_height * presentation.shape[1] / presentation.shape[0])))
    inner_width = min(image_width - 20, inner_width)
    pose_view = cv2.resize(presentation, (inner_width, inner_height), interpolation=cv2.INTER_AREA)
    pose_left = image_left + (image_width - inner_width) // 2
    canvas[image_top + 10 : image_top + 10 + inner_height, pose_left : pose_left + inner_width] = pose_view
    cv2.rectangle(canvas, (image_left, image_top), (image_left + image_width, image_top + image_height), color, 2)

    panel_left, panel_top, panel_width, panel_height = 734, 82, 522, 604
    cv2.rectangle(canvas, (panel_left, panel_top), (panel_left + panel_width, panel_top + panel_height), (17, 23, 30), -1)
    people = assessment.persons
    person = max(people, key=lambda item: item.score) if people else None
    candidate = result.pose_candidates[0] if result.pose_candidates else None
    if person is not None:
        tracking = f"TRACK P{person.track_id}"
        tracking_color = color
    elif candidate is not None:
        tracking = "POSE CANDIDATE / CHECKING"
        tracking_color = CANDIDATE_COLOR
    else:
        tracking = "NO RELIABLE POSE"
        tracking_color = color
    cv2.putText(canvas, tracking, (758, 124), cv2.FONT_HERSHEY_DUPLEX, 0.70, tracking_color, 2, cv2.LINE_AA)
    observed = person.observed_seconds if person is not None else 0.0
    visibility = (
        person.body_visibility
        if person is not None
        else candidate.visibility_score if candidate is not None else 0.0
    )
    cv2.putText(canvas, f"Observed {observed:5.1f} sec", (758, 151), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 191, 203), 1, cv2.LINE_AA)

    center = (1163, 141)
    cv2.ellipse(canvas, center, (52, 52), 0, 0, 360, (42, 51, 62), 9, cv2.LINE_AA)
    cv2.ellipse(canvas, center, (52, 52), -90, 0, 360 * min(1.0, visibility), tracking_color, 9, cv2.LINE_AA)
    visibility_text = f"{visibility * 100:.0f}%"
    text_size = cv2.getTextSize(visibility_text, cv2.FONT_HERSHEY_DUPLEX, 0.62, 1)[0]
    cv2.putText(canvas, visibility_text, (center[0] - text_size[0] // 2, center[1] + 7), cv2.FONT_HERSHEY_DUPLEX, 0.62, (239, 242, 245), 1, cv2.LINE_AA)
    cv2.putText(canvas, "BODY VISIBILITY", (1098, 213), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (145, 160, 176), 1, cv2.LINE_AA)

    head = person.head_drop_change if person is not None else 0.0
    lean = person.lean_change_deg if person is not None else 0.0
    tilt = person.shoulder_tilt_change_deg if person is not None else 0.0
    motion = person.motion_rate if person is not None else 0.0
    signals = person.posture_signal_count if person is not None else 0
    metric_width = 474
    _draw_metric_bar(canvas, x=758, y=252, width=metric_width, label="HEAD DROP CHANGE", value=f"{head:+.3f}", level=head / 0.24, color=color)
    _draw_metric_bar(canvas, x=758, y=314, width=metric_width, label="TORSO LEAN CHANGE", value=f"{lean:4.1f} deg", level=lean / 22.0, color=color)
    _draw_metric_bar(canvas, x=758, y=376, width=metric_width, label="SHOULDER TILT CHANGE", value=f"{tilt:4.1f} deg", level=tilt / 18.0, color=color)
    _draw_metric_bar(canvas, x=758, y=438, width=metric_width, label="WHOLE-BODY MOTION", value=f"{motion:.4f} /s", level=motion / 0.04, color=color)
    _draw_metric_bar(canvas, x=758, y=500, width=metric_width, label="POSTURE SIGNALS", value=f"{signals} / 3", level=signals / 3.0, color=color)

    reason = assessment.reason.replace("_", " ")[:58]
    cv2.rectangle(canvas, (758, 548), (1232, 645), (23, 30, 38), -1)
    cv2.putText(canvas, "CURRENT INTERPRETATION", (774, 576), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (145, 160, 176), 1, cv2.LINE_AA)
    cv2.putText(canvas, reason, (774, 610), cv2.FONT_HERSHEY_DUPLEX, 0.43, color, 1, cv2.LINE_AA)
    cv2.putText(canvas, "NO IDENTITY  /  ADVISORY SIGNALS ONLY", (24, 704), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (126, 138, 152), 1, cv2.LINE_AA)
    return canvas


def _build_single_station_dashboard(
    result: StationResult,
    *,
    state: FatigueState,
    score: int,
    reason: str,
    risk_history: list[int] | None,
) -> np.ndarray:
    """Polished 16:9 operations view with a calm visual hierarchy."""
    bg = (11, 17, 26)
    panel = (21, 30, 42)
    panel2 = (26, 37, 51)
    text = (241, 245, 249)
    muted = (145, 160, 178)
    dashboard = np.full((720, 1280, 3), bg, dtype=np.uint8)
    color = COLORS[state]

    # Header band and restrained brand mark.
    cv2.rectangle(dashboard, (0, 0), (1279, 78), (15, 23, 34), -1)
    cv2.rectangle(dashboard, (24, 20), (30, 59), color, -1)
    cv2.putText(dashboard, "萤石安全视界", (46, 42), cv2.FONT_HERSHEY_DUPLEX, 0.72, text, 2, cv2.LINE_AA)
    cv2.putText(dashboard, "LEFT WORKSTATION  ·  WHOLE-PERSON SAFETY MONITOR", (47, 63), cv2.FONT_HERSHEY_SIMPLEX, 0.37, muted, 1, cv2.LINE_AA)
    state_label = STATE_LABELS[state]
    state_width = cv2.getTextSize(state_label, cv2.FONT_HERSHEY_DUPLEX, 0.48, 1)[0][0]
    pill_left = 1254 - state_width - 26
    cv2.rectangle(dashboard, (pill_left, 22), (1254, 56), panel2, -1)
    cv2.rectangle(dashboard, (pill_left, 22), (pill_left + 5, 56), color, -1)
    cv2.putText(dashboard, state_label, (pill_left + 15, 45), cv2.FONT_HERSHEY_DUPLEX, 0.48, color, 1, cv2.LINE_AA)

    # Main live evidence card.
    video_left, video_top, video_width, video_height = 24, 94, 782, 570
    cv2.rectangle(dashboard, (video_left, video_top), (video_left + video_width, video_top + video_height), panel, -1)
    cv2.putText(dashboard, "LIVE CAMERA", (45, 121), cv2.FONT_HERSHEY_DUPLEX, 0.43, color, 1, cv2.LINE_AA)
    cv2.putText(dashboard, "privacy-filtered · left work area", (153, 121), cv2.FONT_HERSHEY_SIMPLEX, 0.36, muted, 1, cv2.LINE_AA)
    presentation = result.presentation if result.presentation is not None else result.annotated
    video = _letterbox(presentation, video_width - 22, video_height - 52)
    dashboard[video_top + 38 : video_top + video_height - 14, video_left + 11 : video_left + video_width - 11] = video
    cv2.rectangle(dashboard, (video_left, video_top), (video_left + video_width, video_top + video_height), color, 2)
    cv2.circle(dashboard, (video_left + video_width - 31, video_top + 20), 5, (45, 210, 100), -1, cv2.LINE_AA)
    cv2.putText(dashboard, "LIVE", (video_left + video_width - 82, video_top + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (45, 210, 100), 1, cv2.LINE_AA)

    people = result.assessment.persons
    person = max(people, key=lambda item: item.score) if people else None
    observed = person.observed_seconds if person is not None else 0.0
    visibility = person.body_visibility if person is not None else 0.0
    head = person.head_drop_change if person is not None else 0.0
    lean = person.lean_change_deg if person is not None else 0.0
    tilt = person.shoulder_tilt_change_deg if person is not None else 0.0
    motion = person.motion_rate if person is not None else 0.0
    signals = person.posture_signal_count if person is not None else 0

    # Right telemetry column.
    panel_left, panel_top, panel_width, panel_height = 828, 94, 428, 570
    cv2.rectangle(dashboard, (panel_left, panel_top), (panel_left + panel_width, panel_top + panel_height), panel, -1)
    cv2.putText(dashboard, "RISK OVERVIEW", (850, 121), cv2.FONT_HERSHEY_DUPLEX, 0.43, muted, 1, cv2.LINE_AA)
    cv2.putText(dashboard, f"{score:03d}", (850, 178), cv2.FONT_HERSHEY_DUPLEX, 1.52, color, 3, cv2.LINE_AA)
    cv2.putText(dashboard, "/100", (966, 176), cv2.FONT_HERSHEY_DUPLEX, 0.48, muted, 1, cv2.LINE_AA)
    cv2.rectangle(dashboard, (850, 192), (1234, 200), (44, 54, 66), -1)
    cv2.rectangle(dashboard, (850, 192), (850 + int(round(384 * score / 100.0)), 200), color, -1)
    tracking = f"P{person.track_id} TRACKED" if person is not None else "NO RELIABLE POSE"
    cv2.putText(dashboard, tracking, (850, 228), cv2.FONT_HERSHEY_DUPLEX, 0.45, color, 1, cv2.LINE_AA)
    cv2.putText(dashboard, f"observed {observed:4.1f}s   ·   visibility {visibility * 100:3.0f}%", (850, 249), cv2.FONT_HERSHEY_SIMPLEX, 0.35, muted, 1, cv2.LINE_AA)

    # Compact 2×2 signal cards use the empty space more deliberately.
    cards = [
        ("HEAD DROP", f"{head:+.3f}", head / 0.24),
        ("TORSO LEAN", f"{lean:4.1f}°", lean / 22.0),
        ("SHOULDER TILT", f"{tilt:4.1f}°", tilt / 18.0),
        ("BODY MOTION", f"{motion:.4f}/s", motion / 0.04),
    ]
    for idx, (label, value, level) in enumerate(cards):
        x = 850 + (idx % 2) * 194
        y = 272 + (idx // 2) * 72
        cv2.rectangle(dashboard, (x, y), (x + 182, y + 58), panel2, -1)
        cv2.putText(dashboard, label, (x + 12, y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.31, muted, 1, cv2.LINE_AA)
        cv2.putText(dashboard, value, (x + 12, y + 43), cv2.FONT_HERSHEY_DUPLEX, 0.45, text, 1, cv2.LINE_AA)
        bounded = min(1.0, max(0.0, float(level)))
        cv2.rectangle(dashboard, (x + 12, y + 49), (x + 170, y + 53), (48, 58, 70), -1)
        cv2.rectangle(dashboard, (x + 12, y + 49), (x + 12 + int(158 * bounded), y + 53), color, -1)
    cv2.rectangle(dashboard, (850, 416), (1234, 473), panel2, -1)
    cv2.putText(dashboard, "POSTURE SIGNALS", (864, 438), cv2.FONT_HERSHEY_SIMPLEX, 0.32, muted, 1, cv2.LINE_AA)
    cv2.putText(dashboard, f"{signals} / 3", (864, 461), cv2.FONT_HERSHEY_DUPLEX, 0.52, text, 1, cv2.LINE_AA)
    cv2.rectangle(dashboard, (955, 454), (1220, 461), (48, 58, 70), -1)
    cv2.rectangle(dashboard, (955, 454), (955 + int(265 * min(1.0, signals / 3.0)), 461), color, -1)

    cv2.putText(dashboard, "RISK TREND  ·  22 SEC", (850, 501), cv2.FONT_HERSHEY_SIMPLEX, 0.34, muted, 1, cv2.LINE_AA)
    _draw_risk_sparkline(dashboard, risk_history or [score], x=850, y=510, width=384, height=58, color=color)
    alert = {FatigueState.FATIGUE_TREND: "FATIGUE ALERT", FatigueState.HIGH_RISK: "HIGH-RISK BUZZER"}.get(state, "BUZZER SILENT")
    cv2.putText(dashboard, f"●  {alert}", (850, 595), cv2.FONT_HERSHEY_DUPLEX, 0.43, color, 1, cv2.LINE_AA)
    compact_reason = reason.replace("_", " ").replace("upper station:", "")[:48]
    cv2.putText(dashboard, compact_reason, (850, 618), cv2.FONT_HERSHEY_SIMPLEX, 0.33, muted, 1, cv2.LINE_AA)
    cv2.putText(dashboard, "ADVISORY ONLY  ·  FACE PRIVACY ON  ·  NO MACHINE CONTROL", (24, 698), cv2.FONT_HERSHEY_SIMPLEX, 0.34, muted, 1, cv2.LINE_AA)
    return dashboard


def build_dashboard(
    results: list[StationResult],
    source_size: tuple[int, int],
    *,
    recording: bool,
    recording_mode: str | None,
    risk_history: list[int] | None = None,
) -> tuple[np.ndarray, FatigueState, int, str]:
    assessments = {result.station_id: result.assessment for result in results}
    state, score, reason = aggregate_fatigue_assessments(assessments)
    single_station = len(results) == 1
    if single_station:
        dashboard = _build_single_station_dashboard(
            results[0],
            state=state,
            score=score,
            reason=reason,
            risk_history=risk_history,
        )
        return dashboard, state, score, reason
    # Give one active work area the whole display: never leave a misleading
    # empty right-hand tile that looks like a second monitored station.
    tile_width, tile_height = 720, 480
    columns = max(1, min(2, len(results)))
    rows = int(math.ceil(len(results) / columns))
    header_height, footer_height = 92, 58
    dashboard = np.full(
        (header_height + rows * tile_height + footer_height, columns * tile_width, 3),
        (10, 15, 20),
        dtype=np.uint8,
    )
    color = COLORS[state]
    cv2.putText(
        dashboard,
        f"EZVIZ / {'ONE LEFT WORKSTATION' if single_station else 'TWO INDEPENDENT WORKSTATIONS'}  |  {STATE_LABELS[state]}  |  {score}/100",
        (24, 38),
        cv2.FONT_HERSHEY_DUPLEX,
        0.78,
        color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        dashboard,
        f"source {source_size[0]}x{source_size[1]}  |  {reason[:90]}",
        (24, 69),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.47,
        (226, 230, 235),
        1,
        cv2.LINE_AA,
    )
    for index, result in enumerate(results):
        row, column = divmod(index, columns)
        tile = _letterbox(result.annotated, tile_width, tile_height)
        x0, y0 = column * tile_width, header_height + row * tile_height
        dashboard[y0 : y0 + tile_height, x0 : x0 + tile_width] = tile
        cv2.rectangle(
            dashboard,
            (x0, y0),
            (x0 + tile_width - 1, y0 + tile_height - 1),
            COLORS[result.assessment.state],
            3,
        )
    footer_y = dashboard.shape[0] - footer_height
    controls = "S SNAPSHOT   R STOP RECORDING   Q QUIT" if recording else "S SNAPSHOT   R START RECORDING   Q QUIT"
    if recording:
        controls = f"REC {str(recording_mode or '').upper()}   " + controls
    cv2.putText(dashboard, controls, (24, footer_y + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (242, 242, 242), 1, cv2.LINE_AA)
    cv2.putText(dashboard, "FATIGUE-RISK ADVISORY ONLY - NO IDENTITY - NOT A MEDICAL DIAGNOSIS", (24, footer_y + 48), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (190, 195, 205), 1, cv2.LINE_AA)
    return dashboard, state, score, reason


def build_risk_card(state: FatigueState, score: int, reason: str) -> np.ndarray:
    """A deliberately non-video panel separating risk logic from vision."""
    card = np.full((720, 1280, 3), (12, 17, 23), dtype=np.uint8)
    color = COLORS[state]
    cv2.rectangle(card, (0, 0), (1279, 719), color, 12)
    cv2.putText(card, "03  RISK DECISION / LEFT WORKSTATION", (54, 105), cv2.FONT_HERSHEY_DUPLEX, 1.1, color, 3, cv2.LINE_AA)
    cv2.putText(card, STATE_LABELS[state], (54, 255), cv2.FONT_HERSHEY_DUPLEX, 2.0, color, 5, cv2.LINE_AA)
    cv2.putText(card, f"RISK SCORE  {score}/100", (54, 350), cv2.FONT_HERSHEY_SIMPLEX, 1.25, (240, 242, 245), 3, cv2.LINE_AA)
    cv2.putText(card, "INPUT: OpenCV whole-person posture and movement trend", (54, 455), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (180, 190, 204), 2, cv2.LINE_AA)
    cv2.putText(card, "OUTPUT: Arduino / computer buzzer event (UNCERTAIN = SILENT)", (54, 510), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (180, 190, 204), 2, cv2.LINE_AA)
    compact_reason = reason.replace("_", " ")[:92]
    cv2.putText(card, compact_reason, (54, 628), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (220, 224, 230), 1, cv2.LINE_AA)
    return card


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.fps <= 30:
        raise ValueError("--fps must be 1..30")
    if args.dashboard_output_fps <= 0:
        raise ValueError("--dashboard-output-fps must be positive")
    manifest: WideCameraManifest = load_wide_manifest(args.manifest)
    single_station = len(manifest.stations) == 1
    configs = [load_config(spec.config_path) for spec in manifest.stations]
    crop_mode = parse_window_crop(args.window_crop)
    warning_score_min, danger_score_min, threshold_version = load_active_score_thresholds(
        args.active_thresholds_json
    )

    def build_live_capture() -> WindowCaptureDevice:
        if args.source_config:
            from safety_monitor.live_sources import build_source
            return build_source(args.source_config, args.fps)
        return WindowCaptureDevice(
            args.window_helper,
            bundle_id=args.window_app,
            title_contains=args.window_title,
            max_width=1280,
            max_height=960,
            fps=args.fps,
            crop_normalized=None,
            output_aspect_ratio=None,
            # Keep one ScreenCaptureKit stream open for the lifetime of the
            # monitor.  Launching macOS `screencapture` for every frame adds
            # seconds of latency; the named-window stream stays local and still
            # captures only the selected EZVIZ window.
            prefer_composited_fallback=False,
        )

    capture = VisionInputRouter(
        project_root=PROJECT_ROOT,
        mode_path=args.input_mode_json,
        live_factory=build_live_capture,
        fps=args.fps,
    )
    captures_dir = Path(args.captures_dir).expanduser().resolve()
    status_path = Path(args.status_json).expanduser().resolve() if args.status_json else captures_dir / "live-status.json"
    status = MultiStationFatigueStatusWriter(
        status_path,
        context_provider=lambda: {
            **capture.status_context(),
            "threshold_version": threshold_version,
            "warning_min": warning_score_min,
            "danger_min": danger_score_min,
        },
    )
    dashboard_output = (
        Path(args.dashboard_output).expanduser().resolve()
        if args.dashboard_output
        else None
    )
    source_preview_output = (
        Path(args.source_preview_output).expanduser().resolve()
        if args.source_preview_output
        else None
    )
    station_input_output = Path(args.station_input_output).expanduser().resolve() if args.station_input_output else None
    vision_output = Path(args.vision_output).expanduser().resolve() if args.vision_output else None
    risk_card_output = Path(args.risk_card_output).expanduser().resolve() if args.risk_card_output else None
    dashboard_interval_s = 1.0 / args.dashboard_output_fps
    last_dashboard_output_s = float("-inf")
    runtimes: list[StationFatigueRuntime] = []
    manager: CaptureManager | None = None
    started = time.monotonic()
    activity_guard = FrameActivityGuard()
    frame_count = 0
    previous_state: FatigueState | None = None
    risk_history: list[int] = []
    print(
        "萤石安全视界：左侧单工位独立的整人疲劳风险观察。"
        if single_station
        else "萤石安全视界：上下两个工位独立的整人疲劳风险观察。",
        flush=True,
    )
    print("每个工位分别建立约 20 秒基线；手部不再作为主要判断依据。", flush=True)
    print("S = 脱敏截图；R = 开始/停止脱敏录像；Q / Esc = 退出。", flush=True)
    try:
        for index, (spec, config) in enumerate(zip(manifest.stations, configs)):
            runtimes.append(
                StationFatigueRuntime(
                    spec,
                    config,
                    runtime_index=index,
                    baseline_seconds=args.baseline_seconds,
                    trend_seconds=args.trend_seconds,
                    high_risk_seconds=args.high_risk_seconds,
                    warning_score_min=warning_score_min,
                    danger_score_min=danger_score_min,
                )
            )
        while True:
            read = capture.read_observation()
            timestamp_s = time.monotonic() - started
            if not read.ok or read.frame is None:
                faults = {
                    runtime.spec.station_id: runtime.fault(read.reason)
                    for runtime in runtimes
                }
                status.update(faults, timestamp_s)
                print(json.dumps({"state": "UNCERTAIN", "reason": read.reason}, ensure_ascii=False), flush=True)
                break
            raw_frame = read.frame
            frame = extract_video_frame(raw_frame, crop_mode)
            if not is_usable_video_frame(frame):
                activity_guard.reset()
                faults = {
                    runtime.spec.station_id: runtime.fault("video_surface_blank_or_invalid_app_page")
                    for runtime in runtimes
                }
                status.update(faults, timestamp_s)
                # Do not refresh any JPEG: TouchDesigner's freshness switch
                # changes to its explicit waiting page after 1.8 seconds.
                time.sleep(0.08)
                continue
            if not activity_guard.observe(frame, timestamp_s, context=capture.status_context().get("input_mode", "LIVE")):
                status.update({runtime.spec.station_id: runtime.fault("video_content_unchanged")
                               for runtime in runtimes}, timestamp_s)
                # Stop refreshing JPEG evidence; TD's stale-output switch
                # shows waiting. A stationary scene may also trigger this:
                # uncertainty is intentional and must not imply machine safety.
                time.sleep(0.08)
                continue
            if frame_count == 0:
                channel_mean, channel_std = cv2.meanStdDev(frame)
                print(json.dumps({
                    "window_capture_first_frame": True,
                    "width": frame.shape[1],
                    "height": frame.shape[0],
                    "raw_window_width": raw_frame.shape[1],
                    "raw_window_height": raw_frame.shape[0],
                    "window_layout": "portrait_player" if raw_frame.shape[0] / raw_frame.shape[1] > 1.25 and crop_mode == "auto" else "landscape_or_explicit",
                    "mean_bgr": [round(float(value), 2) for value in channel_mean.flatten()],
                    "std_bgr": [round(float(value), 2) for value in channel_std.flatten()],
                    "mode": "ezviz_multistation_whole_person_fatigue",
                }, ensure_ascii=False), flush=True)
            results = [
                runtime.process(frame, frame_index=frame_count, timestamp_s=timestamp_s)
                for runtime in runtimes
            ]
            assessments = {result.station_id: result.assessment for result in results}
            status.update(assessments, timestamp_s)
            _, current_score, _ = aggregate_fatigue_assessments(assessments)
            risk_history.append(current_score)
            if len(risk_history) > 90:
                del risk_history[:-90]
            dashboard, state, score, reason = build_dashboard(
                results,
                (frame.shape[1], frame.shape[0]),
                recording=bool(manager and manager.recording),
                recording_mode=manager.recording_mode if manager else None,
                risk_history=risk_history,
            )
            if (
                (dashboard_output is not None or source_preview_output is not None or station_input_output is not None or vision_output is not None or risk_card_output is not None)
                and timestamp_s - last_dashboard_output_s >= dashboard_interval_s
            ):
                if dashboard_output is not None:
                    write_dashboard_jpeg(dashboard_output, dashboard)
                if source_preview_output is not None:
                    write_dashboard_jpeg(source_preview_output, frame)
                # These three distinct images make the TouchDesigner workflow
                # readable at a glance: crop input -> pose result -> decision.
                if station_input_output is not None and results:
                    station_analysis = build_station_input_analysis(
                        frame,
                        manifest.stations[0],
                        results[0],
                    )
                    write_dashboard_jpeg(station_input_output, station_analysis)
                if vision_output is not None and results:
                    write_dashboard_jpeg(
                        vision_output,
                        build_pose_signal_analysis(results[0]),
                    )
                if risk_card_output is not None:
                    write_dashboard_jpeg(risk_card_output, build_risk_card(state, score, reason))
                last_dashboard_output_s = timestamp_s
            if manager is None:
                manager = CaptureManager(
                    captures_dir,
                    station_id="ezviz_multistation_fatigue",
                    fps=capture.get(cv2.CAP_PROP_FPS) or args.fps,
                    frame_size=(dashboard.shape[1], dashboard.shape[0]),
                    auto_states=("FATIGUE_TREND", "HIGH_RISK"),
                    event_clip_seconds=10.0,
                )
            transitioned = previous_state is not None and previous_state != state
            previous_state = state
            if transitioned:
                print(json.dumps({
                    "state": state.name,
                    "score": score,
                    "reason": reason,
                    "stations": {
                        result.station_id: {
                            "state": result.assessment.state.name,
                            "score": result.assessment.score,
                            "people": len(result.assessment.persons),
                        }
                        for result in results
                    },
                }, ensure_ascii=False), flush=True)
            manager.handle_transition(
                dashboard,
                state=state,
                reason=reason,
                timestamp_s=timestamp_s,
                transitioned=transitioned,
            )
            manager.write(dashboard, timestamp_s)
            if not args.headless:
                window_title = (
                    "EZVIZ Left Workstation Whole-Person Fatigue Risk"
                    if single_station
                    else "EZVIZ Two-Station Whole-Person Fatigue Risk"
                )
                cv2.imshow(window_title, dashboard)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("s"):
                    path = manager.capture_snapshot(dashboard, state=state, reason=reason, kind="manual")
                    print(json.dumps({"snapshot_saved": str(path)}, ensure_ascii=False), flush=True)
                elif key == ord("r"):
                    path = manager.toggle_manual_recording(state=state, reason=reason)
                    print(json.dumps({"manual_recording": "started" if path else "stopped", "path": str(path) if path else None}, ensure_ascii=False), flush=True)
            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break
    finally:
        capture.release()
        for runtime in runtimes:
            runtime.close()
        if manager is not None:
            manager.close()
        cv2.destroyAllWindows()
    print(json.dumps({
        "frames_processed": frame_count,
        "stations": [spec.station_id for spec in manifest.stations],
        "mode": "ezviz_multistation_whole_person_fatigue",
        "status_json": str(status_path),
        "contains_identity": False,
        "medical_diagnosis": False,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
