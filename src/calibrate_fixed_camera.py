#!/usr/bin/env python3
"""Capture a privacy-minimized, people-free reference for a fixed camera."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np

from safety_monitor import load_config
from safety_monitor.camera_device import CameraDevice, redact_source
from safety_monitor.geometry import normalized_to_pixels
from safety_monitor.hand_tracker import HandTracker
from safety_monitor.pose_tracker import PoseTracker
from safety_monitor.wide_station import crop_frame, load_wide_manifest

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Single-station config")
    parser.add_argument("--wide-manifest", help="Wide-camera station crop manifest")
    parser.add_argument("--station-id", help="Station from --wide-manifest")
    parser.add_argument("--source", help="Camera index, video path, or RTSP/HTTP stream URL")
    parser.add_argument("--source-env", help="Environment variable containing the stream URL")
    parser.add_argument(
        "--output",
        help="People-free static-anchor image; defaults inside config/",
    )
    parser.add_argument("--no-config-update", action="store_true")
    return parser.parse_args()


def resolve_relative(value: str, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def main() -> int:
    args = parse_args()
    if bool(args.wide_manifest) != bool(args.station_id):
        raise ValueError("--wide-manifest and --station-id must be used together")
    station_spec = None
    if args.wide_manifest:
        manifest = load_wide_manifest(args.wide_manifest)
        station_spec = next(
            (
                item
                for item in manifest.stations
                if item.station_id == args.station_id
            ),
            None,
        )
        if station_spec is None:
            raise ValueError(f"Unknown station_id: {args.station_id}")
        if not station_spec.crop_verified:
            raise ValueError("Wide-camera station crop must be verified first")
        config_path = station_spec.config_path
    else:
        config_path = Path(
            args.config or PROJECT_ROOT / "config" / "station.example.json"
        ).expanduser().resolve()
    config = load_config(config_path)
    if args.source is not None and args.source_env is not None:
        raise ValueError("Use either --source or --source-env, not both")
    source_raw = os.environ.get(args.source_env) if args.source_env else args.source
    if args.source_env and not source_raw:
        raise ValueError(f"Environment variable {args.source_env!r} is missing or empty")
    source = config.camera_index if source_raw is None else (
        int(source_raw) if source_raw.isdigit() else source_raw
    )
    output = Path(
        args.output
        or PROJECT_ROOT
        / "config"
        / (
            f"fixed-camera-reference-{station_spec.station_id}.png"
            if station_spec is not None
            else "fixed-camera-reference.png"
        )
    ).expanduser().resolve()
    device = CameraDevice(
        source,
        width=config.camera_width,
        height=config.camera_height,
        fps=config.camera_fps,
    )
    if not device.is_opened():
        raise RuntimeError(f"Could not open camera {redact_source(source)}")
    hand_model = resolve_relative(config.model_path)
    pose_model = resolve_relative(config.pose.model_path)
    saved = False
    try:
        with HandTracker(
            hand_model,
            num_hands=config.num_hands,
            min_detection_confidence=config.min_detection_confidence,
            min_presence_confidence=config.min_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
        ) as hands, PoseTracker(
            pose_model,
            num_poses=config.pose.num_poses,
            min_detection_confidence=config.pose.min_detection_confidence,
            min_presence_confidence=config.pose.min_presence_confidence,
            min_tracking_confidence=config.pose.min_tracking_confidence,
            min_landmark_visibility=config.pose.min_landmark_visibility,
        ) as pose:
            sequence = 0
            while True:
                read = device.read_observation()
                if not read.ok or read.frame is None:
                    raise RuntimeError(read.reason)
                frame = cv2.flip(read.frame, 1) if config.mirror else read.frame
                if station_spec is not None:
                    frame = crop_frame(frame, station_spec.crop)
                timestamp_ms = sequence * max(1, int(round(1000 / config.camera_fps)))
                sequence += 1
                hand_observation = hands.detect_observation(frame, timestamp_ms)
                pose_observation = pose.detect(frame, timestamp_ms)
                person_visible = bool(hand_observation.hands or pose_observation is not None)
                preview = frame.copy()
                height, width = preview.shape[:2]
                mask = np.zeros((height, width), dtype=np.uint8)
                for polygon in config.fixed_camera.anchor_rois:
                    points = np.array(normalized_to_pixels(polygon, width, height), np.int32)
                    cv2.polylines(preview, [points], True, (80, 230, 255), 3, cv2.LINE_AA)
                    cv2.fillPoly(mask, [points], 255)
                status = "REMOVE ALL PEOPLE / HANDS" if person_visible else "EMPTY STATION: press S to save anchors"
                color = (40, 40, 235) if person_visible else (70, 210, 90)
                cv2.rectangle(preview, (12, 12), (min(width - 12, 760), 62), (25, 27, 32), -1)
                cv2.putText(preview, status, (28, 47), cv2.FONT_HERSHEY_DUPLEX, 0.72, color, 2, cv2.LINE_AA)
                cv2.imshow("Fixed Camera Reference", preview)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("s"):
                    if person_visible:
                        print("Refused: a hand or upper body is visible. Empty the station first.")
                        continue
                    # Store only configured static anchors, not a full identifiable scene.
                    minimized = np.zeros_like(frame)
                    minimized[mask > 0] = frame[mask > 0]
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(str(output), minimized):
                        raise RuntimeError(f"Could not save {output}")
                    if not args.no_config_update:
                        raw = json.loads(config_path.read_text(encoding="utf-8"))
                        guard = raw.setdefault("camera", {}).setdefault("fixed_guard", {})
                        guard["enabled"] = True
                        try:
                            guard["reference_image"] = str(output.relative_to(PROJECT_ROOT))
                        except ValueError:
                            guard["reference_image"] = str(output)
                        temporary = config_path.with_suffix(config_path.suffix + ".tmp")
                        temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        temporary.replace(config_path)
                    saved = True
                    print(f"Saved privacy-minimized fixed-camera reference: {output}")
                    break
    finally:
        device.release()
        cv2.destroyAllWindows()
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
