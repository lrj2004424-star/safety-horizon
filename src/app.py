#!/usr/bin/env python3
"""Run the joint hand, upper-body and machine-motion offline prototype."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import cv2

from safety_monitor import load_config
from safety_monitor.audio_alerts import AlertSoundManager
from safety_monitor.camera_device import CameraDevice, is_live_source, redact_source
from safety_monitor.capture_manager import CaptureManager, parse_capture_states
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
from safety_monitor.window_capture_device import WindowCaptureDevice

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "station.example.json"))
    parser.add_argument("--source", help="Camera index, local video path, or RTSP/HTTP stream URL")
    parser.add_argument(
        "--source-env",
        help="Read the video source URL from this environment variable so credentials stay out of shell history",
    )
    parser.add_argument(
        "--window-app",
        help="Capture the visible window owned by this macOS bundle identifier instead of a camera/RTSP source",
    )
    parser.add_argument("--window-title", help="Optional title substring used to choose the app window")
    parser.add_argument(
        "--window-helper",
        default=str(PROJECT_ROOT / "macos_window_capture" / "ezviz_window_capture"),
    )
    parser.add_argument("--window-fps", type=int, default=12)
    parser.add_argument("--window-max-width", type=int, default=1280)
    parser.add_argument("--window-max-height", type=int, default=960)
    parser.add_argument(
        "--window-crop",
        type=normalized_crop,
        help="Normalized x0,y0,x1,y1 crop applied to the captured app window",
    )
    parser.add_argument("--hand-model")
    parser.add_argument("--pose-model")
    parser.add_argument("--output-video", help="Write a derived, annotated MP4")
    parser.add_argument("--features-csv", help="Write frame-level anonymous features")
    parser.add_argument(
        "--captures-dir",
        help="Opt in to face-blurred manual snapshots/recording and automatic event captures",
    )
    parser.add_argument(
        "--auto-capture-states",
        default="WARNING,DANGER",
        help="Comma-separated states that trigger a snapshot and short clip",
    )
    parser.add_argument(
        "--event-clip-seconds",
        type=float,
        default=10.0,
        help="Length of each automatic event clip after a configured transition",
    )
    parser.add_argument(
        "--status-json",
        help="Atomically publish the current anonymous state for an output-only indicator bridge",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--mute", action="store_true", help="Disable audible alerts for this run")
    parser.add_argument(
        "--context-only",
        action="store_true",
        help="Run a wide overview camera as evidence collection only; never claim NORMAL/DANGER",
    )
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument(
        "--start-s",
        type=float,
        default=0.0,
        help="For a local video, start at the first frame at or after this source time",
    )
    parser.add_argument(
        "--end-s",
        type=float,
        help="For a local video, stop before this source time",
    )
    return parser.parse_args(argv)


def resolve_relative(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def source_value(raw: str | None, default: int) -> int | str:
    if raw is None:
        return default
    return int(raw) if raw.isdigit() else raw


def resolve_source(raw: str | None, env_name: str | None, default: int) -> int | str:
    if raw is not None and env_name is not None:
        raise ValueError("Use either --source or --source-env, not both")
    if env_name is not None:
        value = os.environ.get(env_name)
        if not value:
            raise ValueError(f"Environment variable {env_name!r} is missing or empty")
        return source_value(value, default)
    return source_value(raw, default)


def normalized_crop(raw: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(value.strip()) for value in raw.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("window crop must be x0,y0,x1,y1") from error
    if len(values) != 4:
        raise argparse.ArgumentTypeError("window crop must contain four comma-separated values")
    x0, y0, x1, y1 = values
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise argparse.ArgumentTypeError("window crop values must be normalized from 0 to 1")
    return x0, y0, x1, y1


def _draw_capture_controls(frame, recording: bool, mode: str | None) -> None:
    height, width = frame.shape[:2]
    label = "S SNAPSHOT   R STOP RECORDING   Q QUIT" if recording else "S SNAPSHOT   R START RECORDING   Q QUIT"
    panel_width = min(width - 36, 510)
    cv2.rectangle(frame, (width - panel_width - 18, height - 84), (width - 18, height - 42), (24, 27, 33), -1)
    if recording:
        cv2.circle(frame, (width - panel_width, height - 63), 8, (30, 30, 235), -1, cv2.LINE_AA)
        label = f"REC {str(mode or '').upper()}   " + label
        x = width - panel_width + 18
    else:
        x = width - panel_width
    cv2.putText(frame, label, (x, height - 56), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1, cv2.LINE_AA)


def _face_blurred_copy(frame, poses):
    output = frame.copy()
    for visible_pose in poses:
        output = blur_face_from_pose(output, visible_pose)
    return output


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(Path(args.config).expanduser().resolve())
    hand_model = resolve_relative(args.hand_model or config.model_path, PROJECT_ROOT)
    pose_model = resolve_relative(args.pose_model or config.pose.model_path, PROJECT_ROOT)
    window_mode = bool(args.window_app)
    if window_mode and (args.source is not None or args.source_env is not None):
        raise ValueError("Use either --window-app or --source/--source-env, not both")
    if not 1 <= args.window_fps <= 30:
        raise ValueError("--window-fps must be between 1 and 30")
    if args.window_max_width < 1 or args.window_max_height < 1:
        raise ValueError("window capture dimensions must be positive")
    source = None if window_mode else resolve_source(args.source, args.source_env, config.camera_index)
    is_live = window_mode or is_live_source(source)
    if args.start_s < 0:
        raise ValueError("--start-s must be non-negative")
    if args.end_s is not None and args.end_s <= args.start_s:
        raise ValueError("--end-s must be greater than --start-s")
    if args.event_clip_seconds <= 0:
        raise ValueError("--event-clip-seconds must be positive")
    capture_states = parse_capture_states(args.auto_capture_states)
    if is_live and (args.start_s > 0 or args.end_s is not None):
        raise ValueError("--start-s/--end-s only apply to local video files")
    events = EventStore(resolve_relative(config.events_dir, PROJECT_ROOT), config.station_id, config.retention_days)
    capture = (
        WindowCaptureDevice(
            args.window_helper,
            bundle_id=args.window_app,
            title_contains=args.window_title,
            max_width=args.window_max_width,
            max_height=args.window_max_height,
            fps=args.window_fps,
            crop_normalized=args.window_crop,
        )
        if window_mode else CameraDevice(
            source,
            width=config.camera_width,
            height=config.camera_height,
            fps=config.camera_fps,
        )
    )
    if not capture.is_opened():
        description = f"macOS window {args.window_app}" if window_mode else redact_source(source)
        raise RuntimeError(f"Could not open video source: {description}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 240:
        fps = config.camera_fps
    if not is_live and args.start_s > 0:
        start_frame = int(math.ceil(args.start_s * fps))
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    capture_manager = (
        CaptureManager(
            args.captures_dir,
            station_id=config.station_id,
            fps=fps,
            frame_size=(width, height),
            auto_states=capture_states,
            event_clip_seconds=args.event_clip_seconds,
        )
        if args.captures_dir else None
    )
    writer = None
    if args.output_video:
        output_path = Path(args.output_video).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise RuntimeError(f"Could not create output video: {output_path}")
    features = FeatureStore(args.features_csv) if args.features_csv else None
    status_output = (
        LiveStatusWriter(Path(args.status_json).expanduser().resolve())
        if args.status_json else None
    )
    engine = JointRiskEngine(config.risk)
    machine_estimator = MachineMotionEstimator(config.machine)
    guard_reference = (
        resolve_relative(config.fixed_camera.reference_image, PROJECT_ROOT)
        if config.fixed_camera.reference_image else None
    )
    camera_guard = FixedCameraGuard(config.fixed_camera, guard_reference)
    truth_path = (
        resolve_relative(config.machine_truth.path, PROJECT_ROOT)
        if config.machine_truth.path else None
    )
    machine_truth = create_machine_truth_provider(config.machine_truth, truth_path)
    audio_alerts = AlertSoundManager(
        config.audio_alerts,
        resolve_relative(config.audio_alerts.assets_dir, PROJECT_ROOT),
    )
    audio_active = (
        not args.mute
        and config.audio_alerts.enabled
        and (is_live or not config.audio_alerts.live_only)
    )
    tracks = TrajectoryBuffer()
    frame_count = 0
    transition_count = 0
    first_source_frame: int | None = None
    last_source_frame: int | None = None
    started = time.monotonic()
    print(
        "Research prototype: advisory display only; no machine control; "
        "missing camera/visibility/machine truth forces UNCERTAIN."
    )
    print(
        f"Audio alerts: {'active' if audio_active and audio_alerts.available else 'inactive'} "
        f"({audio_alerts.status}); NORMAL is always silent."
    )
    try:
        with HandTracker(
            hand_model,
            num_hands=config.num_hands,
            min_detection_confidence=config.min_detection_confidence,
            min_presence_confidence=config.min_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
        ) as hand_tracker, PoseTracker(
            pose_model,
            num_poses=config.pose.num_poses,
            min_detection_confidence=config.pose.min_detection_confidence,
            min_presence_confidence=config.pose.min_presence_confidence,
            min_tracking_confidence=config.pose.min_tracking_confidence,
            min_landmark_visibility=config.pose.min_landmark_visibility,
        ) as pose_tracker:
            while True:
                camera_read = capture.read_observation()
                success, frame = camera_read.ok, camera_read.frame
                if not success or frame is None:
                    if is_live:
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
                if window_mode and frame_count == 0:
                    channel_mean, channel_std = cv2.meanStdDev(frame)
                    print(
                        json.dumps(
                            {
                                "window_capture_first_frame": True,
                                "width": int(frame.shape[1]),
                                "height": int(frame.shape[0]),
                                "mean_bgr": [round(float(value), 2) for value in channel_mean.flatten()],
                                "std_bgr": [round(float(value), 2) for value in channel_std.flatten()],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                source_frame_index = (
                    frame_count
                    if is_live
                    else max(0, int(round(capture.get(cv2.CAP_PROP_POS_FRAMES) - 1)))
                )
                timestamp_s = (
                    time.monotonic() - started
                    if is_live
                    else source_frame_index / fps
                )
                if not is_live and args.end_s is not None and timestamp_s >= args.end_s:
                    break
                frame_count += 1
                first_source_frame = source_frame_index if first_source_frame is None else first_source_frame
                last_source_frame = source_frame_index
                if config.mirror:
                    frame = cv2.flip(frame, 1)
                timestamp_ms = int(timestamp_s * 1000)
                hand_observation = hand_tracker.detect_observation(frame, timestamp_ms)
                pose_observations = pose_tracker.detect_observations(frame, timestamp_ms)
                pose = pose_observations[0] if pose_observations else None
                exclusion_points = [point for hand in hand_observation.hands for point in hand]
                visual_machine = machine_estimator.update(frame, exclusion_points)
                truth = machine_truth.update()
                machine = fuse_machine_observations(
                    visual_machine,
                    truth,
                    required_for_danger=config.machine_truth.required_for_danger,
                )
                guard = camera_guard.update(frame)
                quality_issues: list[str] = []
                if not guard.stable:
                    quality_issues.append(guard.reason)
                if is_live and not config.fixed_camera.enabled:
                    quality_issues.append("fixed_camera_guard_disabled_for_live_source")
                if len(pose_observations) > 1:
                    quality_issues.append("multiple_people_in_single_station_view")
                if args.context_only:
                    quality_issues.append("single_wide_context_view_not_validated")
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
                trajectories = tracks.update(hand_observation.hands)
                if assessment.transition:
                    transition_count += 1
                    events.append_joint(assessment)
                    if (
                        is_live
                        and config.terminal_bell
                        and (not audio_active or not audio_alerts.available)
                        and assessment.state in (JointState.WARNING, JointState.DANGER)
                    ):
                        print("\a", end="", flush=True)
                if features:
                    features.append(source_frame_index, timestamp_s, assessment)
                annotated = draw_joint_overlay(
                    frame,
                    config,
                    assessment,
                    hand_observation.hands,
                    pose,
                    machine,
                    trajectories,
                    draw_risk_zones=not args.context_only,
                    draw_machine_zone=not args.context_only,
                    draw_legend=not args.context_only,
                )
                if args.context_only:
                    banner_width = min(width - 36, 610)
                    cv2.rectangle(
                        annotated,
                        (18, height - 86),
                        (18 + banner_width, height - 42),
                        (24, 27, 33),
                        -1,
                    )
                    cv2.putText(
                        annotated,
                        "CONTEXT ONLY - RISK ZONES NOT CALIBRATED",
                        (34, height - 57),
                        cv2.FONT_HERSHEY_DUPLEX,
                        0.58,
                        (185, 90, 180),
                        2,
                        cv2.LINE_AA,
                    )
                if capture_manager is not None:
                    _draw_capture_controls(
                        annotated,
                        capture_manager.recording,
                        capture_manager.recording_mode,
                    )
                capture_frame = (
                    _face_blurred_copy(annotated, pose_observations)
                    if capture_manager is not None else None
                )
                if capture_manager is not None and capture_frame is not None:
                    capture_manager.handle_transition(
                        capture_frame,
                        state=assessment.state,
                        reason=assessment.reason,
                        timestamp_s=timestamp_s,
                        transitioned=assessment.transition is not None,
                    )
                    capture_manager.write(capture_frame, timestamp_s)
                if writer is not None:
                    output_frame = (
                        _face_blurred_copy(annotated, pose_observations)
                        if config.blur_faces_in_outputs else annotated
                    )
                    writer.write(output_frame)
                if not args.headless:
                    cv2.imshow("Cutting Safety Joint Prototype", annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if capture_manager is not None and capture_frame is not None:
                        if key == ord("s"):
                            path = capture_manager.capture_snapshot(
                                capture_frame,
                                state=assessment.state,
                                reason=assessment.reason,
                                kind="manual",
                            )
                            print(json.dumps({"snapshot_saved": str(path)}, ensure_ascii=False))
                        elif key == ord("r"):
                            path = capture_manager.toggle_manual_recording(
                                state=assessment.state,
                                reason=assessment.reason,
                            )
                            print(
                                json.dumps(
                                    {"manual_recording": "started" if path else "stopped", "path": str(path) if path else None},
                                    ensure_ascii=False,
                                )
                            )
                if args.max_frames and frame_count >= args.max_frames:
                    break
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if features is not None:
            features.close()
        if capture_manager is not None:
            capture_manager.close()
        audio_alerts.close(stop_active=False)
        cv2.destroyAllWindows()
    print(
        json.dumps(
            {
                "frames_processed": frame_count,
                "first_source_frame": first_source_frame,
                "last_source_frame": last_source_frame,
                "source_start_s": args.start_s if not is_live else None,
                "source_end_s": args.end_s if not is_live else None,
                "state_transitions": transition_count,
                "camera_reconnects": capture.reconnect_count,
                "output_video": args.output_video,
                "features_csv": args.features_csv,
                "status_json": args.status_json,
                "captures_dir": args.captures_dir,
                "auto_capture_states": sorted(capture_states),
                "window_app": args.window_app,
                "window_title": args.window_title,
                "context_only": args.context_only,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
