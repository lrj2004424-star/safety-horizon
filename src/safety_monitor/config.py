"""Configuration loading and validation for one monitored workstation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geometry import Point, point_in_polygon


@dataclass(frozen=True)
class RiskConfig:
    danger_zone: tuple[Point, ...]
    warning_zone: tuple[Point, ...]
    relevant_landmarks: tuple[int, ...]
    enter_attention_frames: int
    enter_warning_frames: int
    enter_danger_frames: int
    enter_uncertain_frames: int
    clear_frames: int
    prediction_horizon_s: float
    approach_speed_threshold: float
    speed_smoothing: float
    danger_dwell_s: float
    hand_missing_grace_s: float
    pose_missing_grace_s: float
    min_hand_confidence: float
    forward_lean_change_deg: float
    torso_approach_speed_threshold: float


@dataclass(frozen=True)
class PoseConfig:
    model_path: str
    num_poses: int
    min_detection_confidence: float
    min_presence_confidence: float
    min_tracking_confidence: float
    min_landmark_visibility: float


@dataclass(frozen=True)
class FixedCameraConfig:
    enabled: bool
    reference_image: str | None
    anchor_rois: tuple[tuple[Point, ...], ...]
    check_interval_frames: int
    min_matches: int
    max_translation_px: float
    max_rotation_deg: float
    max_scale_deviation: float


@dataclass(frozen=True)
class MachineConfig:
    motion_roi: tuple[Point, ...]
    reference_roi: tuple[Point, ...]
    global_motion_threshold: float
    min_motion_pixels: float
    vertical_motion_threshold: float
    smoothing: float
    direction_hold_frames: int
    min_directional_coherence: float
    calibrated_with_empty_cycles: bool
    exclusion_radius_px: int
    max_excluded_fraction: float


@dataclass(frozen=True)
class MachineTruthConfig:
    mode: str
    path: str | None
    max_age_s: float
    required_for_danger: bool


@dataclass(frozen=True)
class AudioAlertConfig:
    enabled: bool
    live_only: bool
    assets_dir: str
    volume: float
    startup_grace_s: float
    system_voice_enabled: bool
    system_voice_name: str
    system_voice_rate: int
    attention_repeat_s: float
    warning_repeat_s: float
    danger_repeat_s: float
    uncertain_repeat_s: float
    fault_repeat_s: float


@dataclass(frozen=True)
class StationConfig:
    station_id: str
    camera_index: int
    camera_width: int
    camera_height: int
    camera_fps: float
    mirror: bool
    model_path: str
    num_hands: int
    min_detection_confidence: float
    min_presence_confidence: float
    min_tracking_confidence: float
    fixed_camera: FixedCameraConfig
    pose: PoseConfig
    machine: MachineConfig
    machine_truth: MachineTruthConfig
    audio_alerts: AudioAlertConfig
    risk: RiskConfig
    events_dir: str
    retention_days: int
    terminal_bell: bool
    blur_faces_in_outputs: bool


def _polygon(raw: Any, name: str) -> tuple[Point, ...]:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError(f"{name} must contain at least three [x, y] points")
    result: list[Point] = []
    for index, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"{name}[{index}] must be [x, y]")
        x, y = float(item[0]), float(item[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"{name}[{index}] must use normalized values from 0 to 1")
        result.append((x, y))
    return tuple(result)


def _positive_int(raw: dict[str, Any], key: str, default: int) -> int:
    value = int(raw.get(key, default))
    if value < 1:
        raise ValueError(f"{key} must be at least 1")
    return value


def load_config(path: str | Path) -> StationConfig:
    """Load a JSON config and reject unsafe or ambiguous values early."""
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    camera = raw["camera"]
    model = raw["model"]
    pose = raw.get("pose", {})
    machine = raw.get("machine", {})
    machine_truth = raw.get("machine_truth", {})
    audio_alerts = raw.get("audio_alerts", {})
    risk = raw["risk"]
    privacy = raw["privacy"]
    alerts = raw.get("alerts", {})

    if bool(privacy.get("save_images", False)):
        raise ValueError("This MVP deliberately does not support raw image storage")
    if bool(privacy.get("save_face_identifiers", False)):
        raise ValueError("This MVP deliberately does not support biometric identifiers")
    if bool(alerts.get("machine_control_enabled", False)):
        raise ValueError("This advisory MVP cannot enable machine control")

    danger_zone = _polygon(risk["danger_zone"], "risk.danger_zone")
    warning_zone = _polygon(risk["warning_zone"], "risk.warning_zone")
    if not all(point_in_polygon(point, warning_zone) for point in danger_zone):
        raise ValueError("Every danger-zone vertex must be inside the warning zone")
    motion_roi = _polygon(machine.get("motion_roi", danger_zone), "machine.motion_roi")
    reference_roi = _polygon(
        machine.get("reference_roi", [[0.02, 0.02], [0.28, 0.02], [0.28, 0.2], [0.02, 0.2]]),
        "machine.reference_roi",
    )
    fixed_camera = camera.get("fixed_guard", {})
    anchor_rois_raw = fixed_camera.get("anchor_rois") or [list(map(list, reference_roi))]
    if not isinstance(anchor_rois_raw, list) or not anchor_rois_raw:
        raise ValueError("camera.fixed_guard.anchor_rois must contain at least one polygon")
    anchor_rois = tuple(
        _polygon(value, f"camera.fixed_guard.anchor_rois[{index}]")
        for index, value in enumerate(anchor_rois_raw)
    )

    relevant = tuple(int(value) for value in risk.get("relevant_landmarks", range(21)))
    if not relevant or any(value < 0 or value > 20 for value in relevant):
        raise ValueError("relevant_landmarks must contain MediaPipe indexes from 0 to 20")
    smoothing = float(risk.get("speed_smoothing", 0.55))
    machine_smoothing = float(machine.get("smoothing", 0.5))
    hand_confidence = float(risk.get("min_hand_confidence", 0.5))
    directional_coherence = float(machine.get("min_directional_coherence", 0.62))
    max_excluded_fraction = float(machine.get("max_excluded_fraction", 0.45))
    if not 0.0 <= smoothing <= 1.0 or not 0.0 <= machine_smoothing <= 1.0:
        raise ValueError("smoothing values must be between 0 and 1")
    if not 0.0 <= hand_confidence <= 1.0 or not 0.5 <= directional_coherence <= 1.0:
        raise ValueError("confidence values must be normalized; directional coherence must be at least 0.5")
    if not 0.0 <= max_excluded_fraction < 1.0:
        raise ValueError("machine.max_excluded_fraction must be from 0 (inclusive) to 1 (exclusive)")
    if int(machine.get("exclusion_radius_px", 42)) < 1:
        raise ValueError("machine.exclusion_radius_px must be at least 1")
    truth_mode = str(machine_truth.get("mode", "none"))
    if truth_mode not in {"none", "json_file"}:
        raise ValueError("machine_truth.mode must be 'none' or 'json_file'")
    truth_path = machine_truth.get("path")
    if truth_mode == "json_file" and not truth_path:
        raise ValueError("machine_truth.path is required for json_file mode")
    truth_max_age_s = float(machine_truth.get("max_age_s", 0.25))
    if truth_max_age_s <= 0:
        raise ValueError("machine_truth.max_age_s must be positive")
    for key, value in (
        ("max_translation_px", float(fixed_camera.get("max_translation_px", 8.0))),
        ("max_rotation_deg", float(fixed_camera.get("max_rotation_deg", 1.5))),
        ("max_scale_deviation", float(fixed_camera.get("max_scale_deviation", 0.025))),
    ):
        if value < 0:
            raise ValueError(f"camera.fixed_guard.{key} must be non-negative")
    audio_volume = float(audio_alerts.get("volume", 0.72))
    startup_grace_s = float(audio_alerts.get("startup_grace_s", 2.0))
    if not 0.0 <= audio_volume <= 1.0:
        raise ValueError("audio_alerts.volume must be between 0 and 1")
    if startup_grace_s < 0:
        raise ValueError("audio_alerts.startup_grace_s must be non-negative")
    audio_repeats = {
        key: float(audio_alerts.get(key, default))
        for key, default in (
            ("attention_repeat_s", 15.0),
            ("warning_repeat_s", 12.0),
            ("danger_repeat_s", 10.0),
            ("uncertain_repeat_s", 15.0),
            ("fault_repeat_s", 10.0),
        )
    }
    if any(value <= 0 for value in audio_repeats.values()):
        raise ValueError("audio alert repeat intervals must be positive")
    system_voice_rate = int(audio_alerts.get("system_voice_rate", 210))
    if not 120 <= system_voice_rate <= 320:
        raise ValueError("audio_alerts.system_voice_rate must be between 120 and 320")

    return StationConfig(
        station_id=str(raw["station_id"]),
        camera_index=int(camera.get("index", 0)),
        camera_width=int(camera.get("width", 1280)),
        camera_height=int(camera.get("height", 720)),
        camera_fps=float(camera.get("fps", 30)),
        mirror=bool(camera.get("mirror", False)),
        model_path=str(model["path"]),
        num_hands=int(model.get("num_hands", 2)),
        min_detection_confidence=float(model.get("min_detection_confidence", 0.55)),
        min_presence_confidence=float(model.get("min_presence_confidence", 0.55)),
        min_tracking_confidence=float(model.get("min_tracking_confidence", 0.55)),
        fixed_camera=FixedCameraConfig(
            enabled=bool(fixed_camera.get("enabled", False)),
            reference_image=(
                str(fixed_camera["reference_image"])
                if fixed_camera.get("reference_image") else None
            ),
            anchor_rois=anchor_rois,
            check_interval_frames=_positive_int(fixed_camera, "check_interval_frames", 10),
            min_matches=_positive_int(fixed_camera, "min_matches", 18),
            max_translation_px=float(fixed_camera.get("max_translation_px", 8.0)),
            max_rotation_deg=float(fixed_camera.get("max_rotation_deg", 1.5)),
            max_scale_deviation=float(fixed_camera.get("max_scale_deviation", 0.025)),
        ),
        pose=PoseConfig(
            model_path=str(pose.get("model_path", "models/pose_landmarker_lite.task")),
            num_poses=_positive_int(pose, "num_poses", 1),
            min_detection_confidence=float(pose.get("min_detection_confidence", 0.5)),
            min_presence_confidence=float(pose.get("min_presence_confidence", 0.5)),
            min_tracking_confidence=float(pose.get("min_tracking_confidence", 0.5)),
            min_landmark_visibility=float(pose.get("min_landmark_visibility", 0.45)),
        ),
        machine=MachineConfig(
            motion_roi=motion_roi,
            reference_roi=reference_roi,
            global_motion_threshold=float(machine.get("global_motion_threshold", 2.2)),
            min_motion_pixels=float(machine.get("min_motion_pixels", 0.035)),
            vertical_motion_threshold=float(machine.get("vertical_motion_threshold", 0.45)),
            smoothing=machine_smoothing,
            direction_hold_frames=_positive_int(machine, "direction_hold_frames", 3),
            min_directional_coherence=directional_coherence,
            calibrated_with_empty_cycles=bool(machine.get("calibrated_with_empty_cycles", False)),
            exclusion_radius_px=_positive_int(machine, "exclusion_radius_px", 42),
            max_excluded_fraction=max_excluded_fraction,
        ),
        machine_truth=MachineTruthConfig(
            mode=truth_mode,
            path=str(truth_path) if truth_path else None,
            max_age_s=truth_max_age_s,
            required_for_danger=bool(machine_truth.get("required_for_danger", True)),
        ),
        audio_alerts=AudioAlertConfig(
            enabled=bool(audio_alerts.get("enabled", True)),
            live_only=bool(audio_alerts.get("live_only", True)),
            assets_dir=str(audio_alerts.get("assets_dir", "assets/sounds")),
            volume=audio_volume,
            startup_grace_s=startup_grace_s,
            system_voice_enabled=bool(audio_alerts.get("system_voice_enabled", True)),
            system_voice_name=str(audio_alerts.get("system_voice_name", "Tingting")),
            system_voice_rate=system_voice_rate,
            attention_repeat_s=audio_repeats["attention_repeat_s"],
            warning_repeat_s=audio_repeats["warning_repeat_s"],
            danger_repeat_s=audio_repeats["danger_repeat_s"],
            uncertain_repeat_s=audio_repeats["uncertain_repeat_s"],
            fault_repeat_s=audio_repeats["fault_repeat_s"],
        ),
        risk=RiskConfig(
            danger_zone=danger_zone,
            warning_zone=warning_zone,
            relevant_landmarks=relevant,
            enter_attention_frames=_positive_int(risk, "enter_attention_frames", 3),
            enter_warning_frames=_positive_int(risk, "enter_warning_frames", 3),
            enter_danger_frames=_positive_int(risk, "enter_danger_frames", 2),
            enter_uncertain_frames=_positive_int(risk, "enter_uncertain_frames", 3),
            clear_frames=_positive_int(risk, "clear_frames", 6),
            prediction_horizon_s=float(risk.get("prediction_horizon_s", 0.8)),
            approach_speed_threshold=float(risk.get("approach_speed_threshold", 0.18)),
            speed_smoothing=smoothing,
            danger_dwell_s=float(risk.get("danger_dwell_s", 0.35)),
            hand_missing_grace_s=float(risk.get("hand_missing_grace_s", 0.25)),
            pose_missing_grace_s=float(risk.get("pose_missing_grace_s", 0.5)),
            min_hand_confidence=hand_confidence,
            forward_lean_change_deg=float(risk.get("forward_lean_change_deg", 12.0)),
            torso_approach_speed_threshold=float(risk.get("torso_approach_speed_threshold", 0.08)),
        ),
        events_dir=str(privacy.get("events_dir", "events")),
        retention_days=int(privacy.get("retention_days", 30)),
        terminal_bell=bool(alerts.get("terminal_bell", True)),
        blur_faces_in_outputs=bool(privacy.get("blur_faces_in_outputs", True)),
    )
