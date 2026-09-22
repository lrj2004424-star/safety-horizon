#!/usr/bin/env python3
"""Run the recommended fixed two-camera hand/pose/machine fusion prototype."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

from safety_monitor import load_config
from safety_monitor.audio_alerts import AlertSoundManager
from safety_monitor.camera_device import CameraDevice, is_live_source, redact_source
from safety_monitor.event_store import EventStore
from safety_monitor.feature_store import FeatureStore
from safety_monitor.fixed_camera_guard import FixedCameraGuard
from safety_monitor.hand_tracker import HandTracker
from safety_monitor.joint_engine import JointRiskEngine, JointState
from safety_monitor.joint_visualization import TrajectoryBuffer, draw_joint_overlay
from safety_monitor.machine_motion import MachineMotionEstimator
from safety_monitor.machine_truth import create_machine_truth_provider, fuse_machine_observations
from safety_monitor.pose_tracker import PoseTracker
from safety_monitor.privacy import blur_face_from_pose
from safety_monitor.status_output import LiveStatusWriter

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side-config", default=str(PROJECT_ROOT / "config" / "station.example.json"))
    parser.add_argument("--overhead-config", required=True, help="Separately calibrated overhead hand-zone config")
    parser.add_argument("--side-source", help="Camera index, test-video path, or RTSP/HTTP stream URL")
    parser.add_argument("--overhead-source", help="Camera index, test-video path, or RTSP/HTTP stream URL")
    parser.add_argument("--side-source-env", help="Environment variable containing the side-stream URL")
    parser.add_argument("--overhead-source-env", help="Environment variable containing the overhead-stream URL")
    parser.add_argument("--output-video")
    parser.add_argument("--features-csv")
    parser.add_argument(
        "--status-json",
        help="Atomically publish the current anonymous state for an output-only indicator bridge",
    )
    parser.add_argument(
        "--max-camera-skew-ms",
        type=float,
        default=250.0,
        help="Live camera capture-time skew above this value forces UNCERTAIN",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--mute", action="store_true", help="Disable audible alerts for this run")
    parser.add_argument("--max-frames", type=int, default=0)
    return parser.parse_args()


def resolve_relative(value: str, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def source_value(raw: str | None, default: int) -> int | str:
    if raw is None:
        return default
    return int(raw) if raw.isdigit() else raw


def resolve_source(raw: str | None, env_name: str | None, default: int) -> int | str:
    if raw is not None and env_name is not None:
        raise ValueError("Use either a source argument or its source-env argument, not both")
    if env_name is not None:
        value = os.environ.get(env_name)
        if not value:
            raise ValueError(f"Environment variable {env_name!r} is missing or empty")
        return source_value(value, default)
    return source_value(raw, default)


def _guard(config):
    reference = resolve_relative(config.fixed_camera.reference_image) if config.fixed_camera.reference_image else None
    return FixedCameraGuard(config.fixed_camera, reference)


def _label_view(frame: np.ndarray, label: str) -> None:
    width = frame.shape[1]
    size = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.62, 2)[0]
    x0 = max(8, width - size[0] - 30)
    cv2.rectangle(frame, (x0, 12), (width - 12, 52), (24, 27, 33), -1)
    cv2.putText(frame, label, (x0 + 10, 40), cv2.FONT_HERSHEY_DUPLEX, 0.62, (235, 238, 242), 2, cv2.LINE_AA)


def _join_views(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    target_height = min(left.shape[0], right.shape[0])
    left_width = int(round(left.shape[1] * target_height / left.shape[0]))
    right_width = int(round(right.shape[1] * target_height / right.shape[0]))
    return np.hstack(
        [
            cv2.resize(left, (left_width, target_height), interpolation=cv2.INTER_AREA),
            cv2.resize(right, (right_width, target_height), interpolation=cv2.INTER_AREA),
        ]
    )


def main() -> int:
    args = parse_args()
    side = load_config(Path(args.side_config).expanduser().resolve())
    overhead = load_config(Path(args.overhead_config).expanduser().resolve())
    side_source = resolve_source(args.side_source, args.side_source_env, side.camera_index)
    overhead_source = resolve_source(args.overhead_source, args.overhead_source_env, overhead.camera_index)
    if args.max_camera_skew_ms <= 0:
        raise ValueError("--max-camera-skew-ms must be positive")
    if side_source == overhead_source:
        raise ValueError("Side and overhead cameras must use different video sources")
    sources_are_live = is_live_source(side_source) and is_live_source(overhead_source)
    side_camera = CameraDevice(
        side_source,
        width=side.camera_width,
        height=side.camera_height,
        fps=side.camera_fps,
    )
    overhead_camera = CameraDevice(
        overhead_source,
        width=overhead.camera_width,
        height=overhead.camera_height,
        fps=overhead.camera_fps,
    )
    if not side_camera.is_opened() or not overhead_camera.is_opened():
        side_camera.release()
        overhead_camera.release()
        raise RuntimeError(
            "Could not open both fixed cameras: "
            f"side={redact_source(side_source)}, overhead={redact_source(overhead_source)}"
        )

    engine = JointRiskEngine(overhead.risk)
    machine_estimator = MachineMotionEstimator(side.machine)
    side_guard, overhead_guard = _guard(side), _guard(overhead)
    truth_path = resolve_relative(side.machine_truth.path) if side.machine_truth.path else None
    truth_provider = create_machine_truth_provider(side.machine_truth, truth_path)
    audio_alerts = AlertSoundManager(
        side.audio_alerts,
        resolve_relative(side.audio_alerts.assets_dir),
    )
    audio_active = (
        not args.mute
        and side.audio_alerts.enabled
        and (sources_are_live or not side.audio_alerts.live_only)
    )
    events = EventStore(resolve_relative(side.events_dir), side.station_id, side.retention_days)
    features = FeatureStore(args.features_csv) if args.features_csv else None
    status_output = (
        LiveStatusWriter(Path(args.status_json).expanduser().resolve())
        if args.status_json else None
    )
    trajectories = TrajectoryBuffer()
    writer: cv2.VideoWriter | None = None
    frame_count = 0
    started = time.monotonic()
    hand_timestamp_ms = -1
    pose_timestamp_ms = -1
    overhead_pose_timestamp_ms = -1
    print(
        "Two-camera research prototype: overhead=hands/zones, side=pose/machine; "
        "read-only evidence only; no machine control."
    )
    print(
        f"Audio alerts: {'active' if audio_active and audio_alerts.available else 'inactive'} "
        f"({audio_alerts.status}); NORMAL is always silent."
    )
    if sources_are_live:
        print(
            "Strict live quality gate: both fixed-camera guards must be enabled; "
            f"maximum capture skew={args.max_camera_skew_ms:.0f} ms."
        )
    try:
        with HandTracker(
            resolve_relative(overhead.model_path),
            num_hands=overhead.num_hands,
            min_detection_confidence=overhead.min_detection_confidence,
            min_presence_confidence=overhead.min_presence_confidence,
            min_tracking_confidence=overhead.min_tracking_confidence,
        ) as hands, PoseTracker(
            resolve_relative(side.pose.model_path),
            num_poses=side.pose.num_poses,
            min_detection_confidence=side.pose.min_detection_confidence,
            min_presence_confidence=side.pose.min_presence_confidence,
            min_tracking_confidence=side.pose.min_tracking_confidence,
            min_landmark_visibility=side.pose.min_landmark_visibility,
        ) as side_pose_tracker, PoseTracker(
            resolve_relative(overhead.pose.model_path),
            num_poses=overhead.pose.num_poses,
            min_detection_confidence=overhead.pose.min_detection_confidence,
            min_presence_confidence=overhead.pose.min_presence_confidence,
            min_tracking_confidence=overhead.pose.min_tracking_confidence,
            min_landmark_visibility=overhead.pose.min_landmark_visibility,
        ) as overhead_privacy_pose:
            while True:
                side_read = side_camera.read_observation()
                overhead_read = overhead_camera.read_observation()
                if not side_read.ok or not overhead_read.ok or side_read.frame is None or overhead_read.frame is None:
                    assessment = engine.fault(time.monotonic() - started)
                    if status_output is not None:
                        status_output.update(assessment, time.monotonic() - started)
                    if audio_active:
                        audio_alerts.update(
                            assessment.state,
                            time.monotonic() - started,
                            reason=assessment.reason,
                        )
                    print(json.dumps({"state": assessment.state.name, "reason": assessment.reason}, ensure_ascii=False))
                    break
                side_frame = cv2.flip(side_read.frame, 1) if side.mirror else side_read.frame
                overhead_frame = cv2.flip(overhead_read.frame, 1) if overhead.mirror else overhead_read.frame
                timestamp_s = time.monotonic() - started
                now_ms = int(timestamp_s * 1000)
                hand_timestamp_ms = max(hand_timestamp_ms + 1, now_ms)
                pose_timestamp_ms = max(pose_timestamp_ms + 1, now_ms)
                overhead_pose_timestamp_ms = max(overhead_pose_timestamp_ms + 1, now_ms)
                hand_observation = hands.detect_observation(overhead_frame, hand_timestamp_ms)
                side_poses = side_pose_tracker.detect_observations(side_frame, pose_timestamp_ms)
                pose = side_poses[0] if side_poses else None
                overhead_poses = overhead_privacy_pose.detect_observations(
                    overhead_frame, overhead_pose_timestamp_ms
                )
                # Overhead hand coordinates are not projected into the side view.
                # Therefore they must never be used as a fake side-view flow mask.
                visual_machine = machine_estimator.update(side_frame)
                machine = fuse_machine_observations(
                    visual_machine,
                    truth_provider.update(),
                    required_for_danger=side.machine_truth.required_for_danger,
                )
                side_alignment = side_guard.update(side_frame)
                overhead_alignment = overhead_guard.update(overhead_frame)
                quality_issues = [
                    observation.reason
                    for observation in (side_alignment, overhead_alignment)
                    if not observation.stable
                ]
                if len(side_poses) > 1:
                    quality_issues.append("multiple_people_in_side_view")
                if len(overhead_poses) > 1:
                    quality_issues.append("multiple_people_in_overhead_view")
                if sources_are_live:
                    if not side.fixed_camera.enabled:
                        quality_issues.append("side_fixed_camera_guard_disabled")
                    if not overhead.fixed_camera.enabled:
                        quality_issues.append("overhead_fixed_camera_guard_disabled")
                    camera_skew_s = abs(
                        side_read.captured_monotonic_s - overhead_read.captured_monotonic_s
                    )
                    if camera_skew_s * 1000.0 > args.max_camera_skew_ms:
                        quality_issues.append("camera_streams_out_of_sync")
                assessment = engine.update(
                    hand_observation.hands,
                    pose,
                    machine,
                    timestamp_s,
                    hand_confidence=hand_observation.confidence,
                    quality_issue=";".join(quality_issues) or None,
                )
                if status_output is not None:
                    status_output.update(assessment, timestamp_s)
                audio_update = (
                    audio_alerts.update(
                        assessment.state,
                        timestamp_s,
                        reason=assessment.reason,
                    )
                    if audio_active else None
                )
                if audio_update is not None and audio_update.played:
                    print(
                        json.dumps(
                            {
                                "audio_state": assessment.state.name,
                                "audio_reason": audio_update.reason,
                                "audio_message": audio_update.message,
                            },
                            ensure_ascii=False,
                        )
                    )
                tracks = trajectories.update(hand_observation.hands)
                if assessment.transition:
                    events.append_joint(assessment)
                    if (
                        sources_are_live
                        and side.terminal_bell
                        and (not audio_active or not audio_alerts.available)
                        and assessment.state in (JointState.WARNING, JointState.DANGER)
                    ):
                        print("\a", end="", flush=True)
                if features:
                    features.append(frame_count, timestamp_s, assessment)
                side_annotated = draw_joint_overlay(
                    side_frame,
                    side,
                    assessment,
                    [],
                    pose,
                    machine,
                    [],
                    draw_risk_zones=False,
                    draw_hands=False,
                    draw_legend=False,
                )
                overhead_annotated = draw_joint_overlay(
                    overhead_frame,
                    overhead,
                    assessment,
                    hand_observation.hands,
                    None,
                    machine,
                    tracks,
                    draw_machine_zone=False,
                    draw_pose=False,
                    draw_panel=False,
                )
                _label_view(side_annotated, "SIDE: POSE + MACHINE")
                _label_view(overhead_annotated, "OVERHEAD: HANDS + ZONES")
                if side.blur_faces_in_outputs:
                    for visible_pose in side_poses:
                        side_annotated = blur_face_from_pose(side_annotated, visible_pose)
                    for visible_pose in overhead_poses:
                        overhead_annotated = blur_face_from_pose(overhead_annotated, visible_pose)
                combined = _join_views(side_annotated, overhead_annotated)
                if args.output_video and writer is None:
                    output = Path(args.output_video).expanduser().resolve()
                    output.parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(
                        str(output),
                        cv2.VideoWriter_fourcc(*"mp4v"),
                        min(side.camera_fps, overhead.camera_fps),
                        (combined.shape[1], combined.shape[0]),
                    )
                    if not writer.isOpened():
                        raise RuntimeError(f"Could not create {output}")
                if writer:
                    writer.write(combined)
                if not args.headless:
                    cv2.imshow("Fixed Two-Camera Cutting Safety Prototype", combined)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break
                frame_count += 1
                if args.max_frames and frame_count >= args.max_frames:
                    break
    finally:
        side_camera.release()
        overhead_camera.release()
        if writer:
            writer.release()
        if features:
            features.close()
        audio_alerts.close(stop_active=False)
        cv2.destroyAllWindows()
    print(
        json.dumps(
            {
                "frames_processed": frame_count,
                "side_reconnects": side_camera.reconnect_count,
                "overhead_reconnects": overhead_camera.reconnect_count,
                "output_video": args.output_video,
                "features_csv": args.features_csv,
                "status_json": args.status_json,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
