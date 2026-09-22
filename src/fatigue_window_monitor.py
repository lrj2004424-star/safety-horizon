#!/usr/bin/env python3
"""Observe whole-person fatigue-risk cues in an already-visible EZVIZ window."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2

from safety_monitor import load_config
from safety_monitor.capture_manager import CaptureManager
from safety_monitor.fatigue_engine import FatigueRiskEngine
from safety_monitor.fatigue_status import FatigueStatusWriter
from safety_monitor.fatigue_visualization import draw_fatigue_overlay
from safety_monitor.pose_tracker import PoseTracker
from safety_monitor.privacy import blur_face_from_pose
from safety_monitor.window_capture_device import WindowCaptureDevice


PROJECT_ROOT = Path(__file__).resolve().parent
EZVIZ_BUNDLE_ID = "com.tencent.yybmac.app.com.videogo"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-app", default=EZVIZ_BUNDLE_ID)
    parser.add_argument("--window-title")
    parser.add_argument("--window-helper", default=str(PROJECT_ROOT / "macos_window_capture" / "ezviz_window_capture"))
    parser.add_argument("--window-crop", default="0.0,0.035,1.0,0.345")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "station.example.json"))
    parser.add_argument("--pose-model")
    parser.add_argument("--captures-dir", default=str(PROJECT_ROOT / "artifacts" / "ezviz-window-captures"))
    parser.add_argument("--status-json")
    parser.add_argument("--baseline-seconds", type=float, default=20.0)
    parser.add_argument("--trend-seconds", type=float, default=20.0)
    parser.add_argument("--high-risk-seconds", type=float, default=45.0)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int, default=0)
    return parser.parse_args(argv)


def normalized_crop(raw: str) -> tuple[float, float, float, float] | None:
    if raw.strip().lower() in {"none", "full"}:
        return None
    values = tuple(float(value.strip()) for value in raw.split(","))
    if len(values) != 4:
        raise ValueError("window crop must be x0,y0,x1,y1")
    x0, y0, x1, y1 = values
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("window crop values must be normalized")
    return x0, y0, x1, y1


def _blurred(frame, poses):
    result = frame.copy()
    for pose in poses:
        result = blur_face_from_pose(result, pose)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.fps <= 30:
        raise ValueError("--fps must be 1..30")
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    default_full_model = PROJECT_ROOT / "models" / "pose_landmarker_full.task"
    pose_model = Path(
        args.pose_model
        or (str(default_full_model) if default_full_model.is_file() else config.pose.model_path)
    ).expanduser()
    if not pose_model.is_absolute():
        pose_model = (PROJECT_ROOT / pose_model).resolve()
    captures_dir = Path(args.captures_dir).expanduser().resolve()
    status_path = Path(args.status_json).expanduser().resolve() if args.status_json else captures_dir / "live-status.json"
    capture = WindowCaptureDevice(
        args.window_helper,
        bundle_id=args.window_app,
        title_contains=args.window_title,
        max_width=1280,
        max_height=960,
        fps=args.fps,
        crop_normalized=normalized_crop(args.window_crop),
        output_aspect_ratio=16.0 / 9.0,
    )
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS) or float(args.fps)
    manager = CaptureManager(
        captures_dir,
        station_id="ezviz_fatigue_observer",
        fps=fps,
        frame_size=(width, height),
        auto_states=("FATIGUE_TREND", "HIGH_RISK"),
        event_clip_seconds=10.0,
    )
    status = FatigueStatusWriter(status_path)
    engine = FatigueRiskEngine(
        baseline_seconds=args.baseline_seconds,
        trend_seconds=args.trend_seconds,
        high_risk_seconds=args.high_risk_seconds,
        max_people=config.pose.num_poses,
        min_body_visibility=min(0.30, config.pose.min_landmark_visibility),
    )
    started = time.monotonic()
    frame_count = 0
    print("员工疲劳风险观察模式：不再使用手部危险区判断。", flush=True)
    print("前 20 秒建立个人工作基线；结果是安全提示，不是医学诊断。", flush=True)
    print("S = 脱敏截图；R = 开始/停止脱敏录像；Q / Esc = 退出。", flush=True)
    try:
        with PoseTracker(
            pose_model,
            num_poses=config.pose.num_poses,
            min_detection_confidence=min(0.40, config.pose.min_detection_confidence),
            min_presence_confidence=min(0.40, config.pose.min_presence_confidence),
            min_tracking_confidence=min(0.40, config.pose.min_tracking_confidence),
            min_landmark_visibility=min(0.30, config.pose.min_landmark_visibility),
        ) as pose_tracker:
            while True:
                read = capture.read_observation()
                timestamp_s = time.monotonic() - started
                if not read.ok or read.frame is None:
                    assessment = engine.fault(read.reason)
                    status.update(assessment, timestamp_s)
                    print(json.dumps({"state": assessment.state.name, "reason": assessment.reason}, ensure_ascii=False), flush=True)
                    break
                frame = read.frame
                if frame_count == 0:
                    channel_mean, channel_std = cv2.meanStdDev(frame)
                    print(json.dumps({
                        "window_capture_first_frame": True,
                        "width": frame.shape[1],
                        "height": frame.shape[0],
                        "raw_window_width": capture.raw_width,
                        "raw_window_height": capture.raw_height,
                        "mean_bgr": [round(float(value), 2) for value in channel_mean.flatten()],
                        "std_bgr": [round(float(value), 2) for value in channel_std.flatten()],
                        "mode": "whole_person_fatigue_risk",
                    }, ensure_ascii=False), flush=True)
                frame_count += 1
                poses = pose_tracker.detect_observations(frame, int(timestamp_s * 1000))
                assessment = engine.update(poses, timestamp_s)
                status.update(assessment, timestamp_s)
                if assessment.transition is not None:
                    print(json.dumps({
                        "state": assessment.state.name,
                        "score": assessment.score,
                        "visible_people": len(assessment.persons),
                        "reason": assessment.reason,
                    }, ensure_ascii=False), flush=True)
                annotated = draw_fatigue_overlay(
                    frame,
                    assessment,
                    recording=manager.recording,
                    recording_mode=manager.recording_mode,
                )
                private_frame = _blurred(annotated, poses)
                manager.handle_transition(
                    private_frame,
                    state=assessment.state,
                    reason=assessment.reason,
                    timestamp_s=timestamp_s,
                    transitioned=assessment.transition is not None,
                )
                manager.write(private_frame, timestamp_s)
                if not args.headless:
                    cv2.imshow("EZVIZ Whole-Person Fatigue Risk", annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord("s"):
                        path = manager.capture_snapshot(private_frame, state=assessment.state, reason=assessment.reason, kind="manual")
                        print(json.dumps({"snapshot_saved": str(path)}, ensure_ascii=False), flush=True)
                    elif key == ord("r"):
                        path = manager.toggle_manual_recording(state=assessment.state, reason=assessment.reason)
                        print(json.dumps({"manual_recording": "started" if path else "stopped", "path": str(path) if path else None}, ensure_ascii=False), flush=True)
                if args.max_frames and frame_count >= args.max_frames:
                    break
    finally:
        manager.close()
        capture.release()
        cv2.destroyAllWindows()
    print(json.dumps({
        "frames_processed": frame_count,
        "mode": "whole_person_fatigue_risk",
        "status_json": str(status_path),
        "captures_dir": str(captures_dir),
        "contains_identity": False,
        "medical_diagnosis": False,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
