#!/usr/bin/env python3
"""Run independent safety pipelines for several crops from one fixed wide camera."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from safety_monitor.audio_alerts import AlertSoundManager
from safety_monitor.camera_device import CameraDevice, is_live_source, redact_source
from safety_monitor.capture_manager import CaptureManager, parse_capture_states
from safety_monitor.config import StationConfig, load_config
from safety_monitor.event_store import EventStore
from safety_monitor.feature_store import FeatureStore
from safety_monitor.fixed_camera_guard import FixedCameraGuard
from safety_monitor.hand_tracker import HandTracker
from safety_monitor.joint_engine import JointAssessment, JointRiskEngine, JointState
from safety_monitor.joint_visualization import TrajectoryBuffer, draw_joint_overlay
from safety_monitor.machine_motion import MachineMotionEstimator
from safety_monitor.machine_truth import create_machine_truth_provider, fuse_machine_observations
from safety_monitor.pose_tracker import PoseObservation, PoseTracker
from safety_monitor.privacy import blur_face_from_pose
from safety_monitor.status_output import (
    MultiStationStatusWriter,
    aggregate_station_assessments,
)
from safety_monitor.wide_station import (
    WideCameraManifest,
    WideStationSpec,
    crop_frame,
    load_wide_manifest,
    station_quality_issues,
)


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_COLORS = {
    JointState.NORMAL: (72, 179, 73),
    JointState.ATTENTION: (0, 210, 255),
    JointState.WARNING: (0, 140, 255),
    JointState.DANGER: (40, 40, 235),
    JointState.UNCERTAIN: (185, 90, 180),
    JointState.FAULT: (90, 90, 90),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "config" / "video32-wide.example.json"),
    )
    parser.add_argument("--source", help="Camera index, local video/image, or RTSP/HTTP URL")
    parser.add_argument("--source-env", help="Environment variable containing the stream URL")
    parser.add_argument("--output-video", help="Write a face-blurred multi-station dashboard")
    parser.add_argument("--features-dir", help="Write one anonymous feature CSV per station")
    parser.add_argument("--captures-dir", help="Opt in to blurred dashboard snapshots/event clips")
    parser.add_argument("--auto-capture-states", default="WARNING,DANGER")
    parser.add_argument("--event-clip-seconds", type=float, default=10.0)
    parser.add_argument("--status-json", help="Publish aggregate and per-station anonymous state")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--mute", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--start-s", type=float, default=0.0)
    parser.add_argument("--end-s", type=float)
    return parser.parse_args(argv)


def _resolve_relative(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _source_value(raw: str | None, default: int) -> int | str:
    if raw is None:
        return default
    return int(raw) if raw.isdigit() else raw


def _resolve_source(raw: str | None, env_name: str | None, default: int) -> int | str:
    if raw is not None and env_name is not None:
        raise ValueError("Use either --source or --source-env, not both")
    if env_name is not None:
        value = os.environ.get(env_name)
        if not value:
            raise ValueError(f"Environment variable {env_name!r} is missing or empty")
        return _source_value(value, default)
    return _source_value(raw, default)


@dataclass
class StationFrame:
    station_id: str
    annotated: np.ndarray
    assessment: JointAssessment
    poses: list[PoseObservation]


class StationRuntime:
    """One fully isolated detector/state/log pipeline inside a wide-camera crop."""

    def __init__(
        self,
        manifest: WideCameraManifest,
        spec: WideStationSpec,
        config: StationConfig,
        *,
        features_path: Path | None,
    ) -> None:
        self.manifest = manifest
        self.spec = spec
        self.config = config
        self.engine = JointRiskEngine(config.risk)
        self.machine_estimator = MachineMotionEstimator(config.machine)
        guard_reference = (
            _resolve_relative(config.fixed_camera.reference_image)
            if config.fixed_camera.reference_image else None
        )
        self.guard = FixedCameraGuard(config.fixed_camera, guard_reference)
        truth_path = (
            _resolve_relative(config.machine_truth.path)
            if config.machine_truth.path else None
        )
        self.machine_truth = create_machine_truth_provider(
            config.machine_truth, truth_path
        )
        self.hands = HandTracker(
            _resolve_relative(config.model_path),
            num_hands=config.num_hands,
            min_detection_confidence=config.min_detection_confidence,
            min_presence_confidence=config.min_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
        )
        self.pose = PoseTracker(
            _resolve_relative(config.pose.model_path),
            num_poses=max(2, config.pose.num_poses),
            min_detection_confidence=config.pose.min_detection_confidence,
            min_presence_confidence=config.pose.min_presence_confidence,
            min_tracking_confidence=config.pose.min_tracking_confidence,
            min_landmark_visibility=config.pose.min_landmark_visibility,
        )
        self.trajectories = TrajectoryBuffer()
        self._cached_poses: list[PoseObservation] = []
        self._last_pose_frame_index: int | None = None
        self.events = EventStore(
            _resolve_relative(config.events_dir),
            spec.station_id,
            config.retention_days,
        )
        self.features = FeatureStore(features_path) if features_path else None

    def close(self) -> None:
        self.hands.close()
        self.pose.close()
        if self.features is not None:
            self.features.close()

    def fault(self, timestamp_s: float) -> JointAssessment:
        return self.engine.fault(timestamp_s)

    def process(
        self,
        source_frame: np.ndarray,
        *,
        frame_index: int,
        timestamp_s: float,
        timestamp_ms: int,
        is_live: bool,
    ) -> StationFrame:
        crop = crop_frame(source_frame, self.spec.crop)
        hand_observation = self.hands.detect_observation(crop, timestamp_ms)
        pose_updated = (
            self._last_pose_frame_index is None
            or frame_index - self._last_pose_frame_index
            >= self.spec.pose_interval_frames
        )
        if pose_updated:
            self._cached_poses = self.pose.detect_observations(crop, timestamp_ms)
            self._last_pose_frame_index = frame_index
        poses = self._cached_poses
        primary_pose = (
            max(poses, key=lambda observation: observation.visibility_score)
            if poses else None
        )
        exclusion_points = [
            point for hand in hand_observation.hands for point in hand
        ]
        visual_machine = self.machine_estimator.update(crop, exclusion_points)
        machine = fuse_machine_observations(
            visual_machine,
            self.machine_truth.update(),
            required_for_danger=self.config.machine_truth.required_for_danger,
        )
        quality_issues = list(
            station_quality_issues(
                self.manifest,
                self.spec,
                source_frame.shape[1],
                source_frame.shape[0],
            )
        )
        alignment = self.guard.update(crop)
        if not alignment.stable:
            quality_issues.append(alignment.reason)
        if is_live and not self.config.fixed_camera.enabled:
            quality_issues.append("fixed_camera_guard_disabled_for_live_source")
        if len(poses) > 1:
            quality_issues.append("multiple_people_in_station_crop")
        assessment = self.engine.update(
            hand_observation.hands,
            primary_pose,
            machine,
            timestamp_s,
            hand_confidence=hand_observation.confidence,
            quality_issue=";".join(dict.fromkeys(quality_issues)) or None,
            pose_updated=pose_updated,
        )
        tracks = self.trajectories.update(hand_observation.hands)
        if assessment.transition is not None:
            self.events.append_joint(assessment)
        if self.features is not None:
            self.features.append(frame_index, timestamp_s, assessment)

        privacy_frame = crop.copy()
        for visible_pose in poses:
            privacy_frame = blur_face_from_pose(privacy_frame, visible_pose)
        calibrated = self.spec.crop_verified and self.spec.zones_calibrated
        annotated = draw_joint_overlay(
            privacy_frame,
            self.config,
            assessment,
            hand_observation.hands,
            primary_pose,
            machine,
            tracks,
            draw_risk_zones=calibrated,
            draw_machine_zone=calibrated,
            draw_panel=False,
            draw_legend=False,
        )
        _draw_station_header(
            annotated,
            self.spec.station_id,
            assessment,
            calibrated=calibrated,
        )
        return StationFrame(self.spec.station_id, annotated, assessment, poses)


def _draw_station_header(
    frame: np.ndarray,
    station_id: str,
    assessment: JointAssessment,
    *,
    calibrated: bool,
) -> None:
    height, width = frame.shape[:2]
    color = STATE_COLORS[assessment.state]
    cv2.rectangle(frame, (0, 0), (width, min(height, 74)), (24, 27, 33), -1)
    cv2.rectangle(frame, (0, 0), (9, min(height, 74)), color, -1)
    cv2.putText(
        frame,
        f"{station_id}  |  {assessment.state.name}",
        (22, 31),
        cv2.FONT_HERSHEY_DUPLEX,
        0.62,
        color,
        2,
        cv2.LINE_AA,
    )
    reason = assessment.reason.replace("_", " ")
    cv2.putText(
        frame,
        reason[:70],
        (22, 58),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
        (232, 232, 232),
        1,
        cv2.LINE_AA,
    )
    if not calibrated:
        label = "CROP / RISK ZONES NOT VERIFIED"
        size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0]
        y0 = max(76, height - 42)
        cv2.rectangle(frame, (0, y0), (min(width, size[0] + 28), height), (24, 27, 33), -1)
        cv2.putText(
            frame,
            label,
            (14, min(height - 13, y0 + 27)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (185, 90, 180),
            1,
            cv2.LINE_AA,
        )


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
    canvas[y0:y0 + target_height, x0:x0 + target_width] = resized
    return canvas


def _dashboard(
    station_frames: list[StationFrame],
    source_width: int,
    source_height: int,
) -> tuple[np.ndarray, JointState, str]:
    assessments = {
        station.station_id: station.assessment for station in station_frames
    }
    aggregate_state, aggregate_reason = aggregate_station_assessments(assessments)
    tile_width, tile_height = 720, 480
    columns = min(2, len(station_frames))
    rows = int(math.ceil(len(station_frames) / columns))
    header_height = 82
    dashboard = np.full(
        (header_height + rows * tile_height, columns * tile_width, 3),
        (10, 15, 20),
        dtype=np.uint8,
    )
    color = STATE_COLORS[aggregate_state]
    cv2.putText(
        dashboard,
        f"WIDE CAMERA / INDEPENDENT STATIONS  |  {aggregate_state.name}",
        (24, 36),
        cv2.FONT_HERSHEY_DUPLEX,
        0.78,
        color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        dashboard,
        f"source {source_width}x{source_height}  |  {aggregate_reason[:95]}  |  ADVISORY ONLY - NO MACHINE CONTROL",
        (24, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (225, 230, 235),
        1,
        cv2.LINE_AA,
    )
    for index, station in enumerate(station_frames):
        row, column = divmod(index, columns)
        tile = _letterbox(station.annotated, tile_width, tile_height)
        y0 = header_height + row * tile_height
        x0 = column * tile_width
        dashboard[y0:y0 + tile_height, x0:x0 + tile_width] = tile
        cv2.rectangle(
            dashboard,
            (x0, y0),
            (x0 + tile_width - 1, y0 + tile_height - 1),
            STATE_COLORS[station.assessment.state],
            3,
        )
    return dashboard, aggregate_state, aggregate_reason


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.start_s < 0:
        raise ValueError("--start-s must be non-negative")
    if args.end_s is not None and args.end_s <= args.start_s:
        raise ValueError("--end-s must be greater than --start-s")
    if args.event_clip_seconds <= 0:
        raise ValueError("--event-clip-seconds must be positive")
    manifest = load_wide_manifest(args.manifest)
    configs = [load_config(spec.config_path) for spec in manifest.stations]
    mirror_values = {config.mirror for config in configs}
    if len(mirror_values) != 1:
        raise ValueError("All station configs for one wide camera must use the same mirror setting")
    first_config = configs[0]
    source = _resolve_source(
        args.source, args.source_env, first_config.camera_index
    )
    live = is_live_source(source)
    if live and (args.start_s > 0 or args.end_s is not None):
        raise ValueError("--start-s/--end-s only apply to local files")
    capture_states = parse_capture_states(args.auto_capture_states)
    capture = CameraDevice(
        source,
        width=first_config.camera_width,
        height=first_config.camera_height,
        fps=first_config.camera_fps,
    )
    if not capture.is_opened():
        raise RuntimeError(f"Could not open source: {redact_source(source)}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 240:
        fps = first_config.camera_fps
    if not live and args.start_s > 0:
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(math.ceil(args.start_s * fps)))

    features_dir = (
        Path(args.features_dir).expanduser().resolve()
        if args.features_dir else None
    )
    if features_dir is not None:
        features_dir.mkdir(parents=True, exist_ok=True)
    runtimes: list[StationRuntime] = []
    writer: cv2.VideoWriter | None = None
    capture_manager: CaptureManager | None = None
    status_writer = (
        MultiStationStatusWriter(Path(args.status_json).expanduser().resolve())
        if args.status_json else None
    )
    audio = AlertSoundManager(
        first_config.audio_alerts,
        _resolve_relative(first_config.audio_alerts.assets_dir),
    )
    audio_active = (
        not args.mute
        and first_config.audio_alerts.enabled
        and (live or not first_config.audio_alerts.live_only)
    )
    frame_count = 0
    started = time.monotonic()
    previous_aggregate: JointState | None = None
    print(
        "Single-wide-camera multi-station research prototype: each crop has an "
        "independent hand/pose/machine/state pipeline; no machine control."
    )
    try:
        for spec, config in zip(manifest.stations, configs):
            feature_path = (
                features_dir / f"{spec.station_id}.csv"
                if features_dir is not None else None
            )
            runtimes.append(
                StationRuntime(
                    manifest,
                    spec,
                    config,
                    features_path=feature_path,
                )
            )
        while True:
            read = capture.read_observation()
            timestamp_s = time.monotonic() - started
            if not read.ok or read.frame is None:
                if live:
                    faults = {
                        runtime.spec.station_id: runtime.fault(timestamp_s)
                        for runtime in runtimes
                    }
                    aggregate_state, aggregate_reason = aggregate_station_assessments(
                        faults
                    )
                    if status_writer is not None:
                        status_writer.update(faults, timestamp_s)
                    if audio_active:
                        audio.update(
                            aggregate_state,
                            timestamp_s,
                            reason=aggregate_reason,
                        )
                    print(
                        json.dumps(
                            {
                                "state": aggregate_state.name,
                                "reason": aggregate_reason,
                            },
                            ensure_ascii=False,
                        )
                    )
                break
            frame = cv2.flip(read.frame, 1) if first_config.mirror else read.frame
            source_frame_index = (
                frame_count
                if live
                else max(0, int(round(capture.get(cv2.CAP_PROP_POS_FRAMES) - 1)))
            )
            timestamp_s = (
                time.monotonic() - started
                if live else source_frame_index / fps
            )
            if not live and args.end_s is not None and timestamp_s >= args.end_s:
                break
            timestamp_ms = max(0, int(round(timestamp_s * 1000)))
            station_frames = [
                runtime.process(
                    frame,
                    frame_index=source_frame_index,
                    timestamp_s=timestamp_s,
                    timestamp_ms=timestamp_ms,
                    is_live=live,
                )
                for runtime in runtimes
            ]
            dashboard, aggregate_state, aggregate_reason = _dashboard(
                station_frames,
                frame.shape[1],
                frame.shape[0],
            )
            assessments = {
                station.station_id: station.assessment
                for station in station_frames
            }
            if status_writer is not None:
                status_writer.update(assessments, timestamp_s)
            if audio_active:
                audio.update(
                    aggregate_state,
                    timestamp_s,
                    reason=aggregate_reason,
                )
            aggregate_transition = (
                previous_aggregate is not None
                and previous_aggregate != aggregate_state
            )
            previous_aggregate = aggregate_state
            if writer is None and args.output_video:
                output_path = Path(args.output_video).expanduser().resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(
                    str(output_path),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (dashboard.shape[1], dashboard.shape[0]),
                )
                if not writer.isOpened():
                    raise RuntimeError(f"Could not create output video: {output_path}")
            if capture_manager is None and args.captures_dir:
                capture_manager = CaptureManager(
                    args.captures_dir,
                    station_id="wide_camera_multistation",
                    fps=fps,
                    frame_size=(dashboard.shape[1], dashboard.shape[0]),
                    auto_states=capture_states,
                    event_clip_seconds=args.event_clip_seconds,
                )
            if capture_manager is not None:
                capture_manager.handle_transition(
                    dashboard,
                    state=aggregate_state,
                    reason=aggregate_reason,
                    timestamp_s=timestamp_s,
                    transitioned=aggregate_transition,
                )
                capture_manager.write(dashboard, timestamp_s)
            if writer is not None:
                writer.write(dashboard)
            if not args.headless:
                cv2.imshow("Wide Camera Independent Workstations", dashboard)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if capture_manager is not None:
                    if key == ord("s"):
                        path = capture_manager.capture_snapshot(
                            dashboard,
                            state=aggregate_state,
                            reason=aggregate_reason,
                            kind="manual",
                        )
                        print(json.dumps({"snapshot_saved": str(path)}, ensure_ascii=False))
                    elif key == ord("r"):
                        path = capture_manager.toggle_manual_recording(
                            state=aggregate_state,
                            reason=aggregate_reason,
                        )
                        print(
                            json.dumps(
                                {
                                    "manual_recording": "started" if path else "stopped",
                                    "path": str(path) if path else None,
                                },
                                ensure_ascii=False,
                            )
                        )
            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break
    finally:
        capture.release()
        for runtime in runtimes:
            runtime.close()
        if writer is not None:
            writer.release()
        if capture_manager is not None:
            capture_manager.close()
        audio.close(stop_active=False)
        cv2.destroyAllWindows()
    print(
        json.dumps(
            {
                "frames_processed": frame_count,
                "stations": [spec.station_id for spec in manifest.stations],
                "source": redact_source(source),
                "camera_reconnects": capture.reconnect_count,
                "output_video": args.output_video,
                "features_dir": args.features_dir,
                "status_json": args.status_json,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
