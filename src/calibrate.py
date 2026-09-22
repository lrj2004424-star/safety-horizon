#!/usr/bin/env python3
"""Click warning, danger, machine-motion and reference polygons."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np

from safety_monitor.camera_device import redact_source
from safety_monitor.config import load_config
from safety_monitor.geometry import point_in_polygon
from safety_monitor.wide_station import (
    crop_frame,
    load_wide_manifest,
    update_manifest_station,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        help="Single-station config; omit when --wide-manifest is used",
    )
    parser.add_argument("--wide-manifest", help="Wide-camera station crop manifest")
    parser.add_argument("--station-id", help="Station from --wide-manifest to calibrate")
    parser.add_argument("--source", default=None, help="Camera index, video path, or RTSP/HTTP stream URL")
    parser.add_argument("--source-env", help="Environment variable containing the stream URL")
    return parser.parse_args()


def source_value(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def main() -> int:
    args = parse_args()
    if bool(args.wide_manifest) != bool(args.station_id):
        raise ValueError("--wide-manifest and --station-id must be used together")
    manifest = None
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
            raise ValueError(
                "Calibrate the wide-camera station crop before calibrating risk zones"
            )
        config_path = station_spec.config_path
    else:
        config_path = Path(
            args.config or PROJECT_ROOT / "config" / "station.example.json"
        ).expanduser().resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    station_config = load_config(config_path)
    if args.source is not None and args.source_env is not None:
        raise ValueError("Use either --source or --source-env, not both")
    source_raw = (
        os.environ.get(args.source_env)
        if args.source_env
        else (args.source or str(station_config.camera_index))
    )
    if not source_raw:
        raise ValueError(f"Environment variable {args.source_env!r} is missing or empty")
    source = source_value(source_raw)
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open source: {redact_source(source)}")

    stage_names = ["WARNING", "DANGER", "MACHINE MOTION", "STATIC REFERENCE"]
    colors = [(0, 196, 255), (48, 48, 235), (230, 120, 45), (180, 180, 180)]
    polygons: list[list[tuple[int, int]]] = [[], [], [], []]
    stage = 0
    window = "Calibrate zones"

    def on_mouse(event: int, x: int, y: int, _flags: int, _data: object) -> None:
        if stage >= len(stage_names):
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            polygons[stage].append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and polygons[stage]:
            polygons[stage].pop()

    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)
    latest: np.ndarray | None = None
    try:
        while True:
            success, frame = capture.read()
            if success and frame is not None:
                if station_config.mirror:
                    frame = cv2.flip(frame, 1)
                latest = (
                    crop_frame(frame, station_spec.crop)
                    if station_spec is not None
                    else frame
                )
            if latest is None:
                continue
            display = latest.copy()
            for index, polygon in enumerate(polygons):
                if polygon:
                    points = np.array(polygon, np.int32)
                    cv2.polylines(
                        display,
                        [points],
                        len(polygon) >= 3,
                        colors[index],
                        3,
                        cv2.LINE_AA,
                    )
                    for point in polygon:
                        cv2.circle(display, point, 5, colors[index], -1)
            title = "READY TO SAVE" if stage >= len(stage_names) else f"DRAW {stage_names[stage]} ZONE"
            cv2.rectangle(display, (12, 12), (740, 82), (20, 22, 28), -1)
            cv2.putText(
                display,
                title,
                (28, 42),
                cv2.FONT_HERSHEY_DUPLEX,
                0.85,
                colors[min(stage, 1)],
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                display,
                "Left click: add | Right click: undo | Enter: next/save | Esc: cancel",
                (28, 68),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (230, 230, 230),
                1,
                cv2.LINE_AA,
            )
            cv2.imshow(window, display)
            key = cv2.waitKey(20) & 0xFF
            if key == 27:
                print("Calibration cancelled; config was not changed.")
                return 1
            if key in (10, 13):
                if stage < len(stage_names) and len(polygons[stage]) < 3:
                    print(f"{stage_names[stage]} needs at least three points.")
                    continue
                stage += 1
                if stage >= len(stage_names):
                    height, width = latest.shape[:2]
                    warning_zone = [
                        [round(x / (width - 1), 6), round(y / (height - 1), 6)]
                        for x, y in polygons[0]
                    ]
                    danger_zone = [
                        [round(x / (width - 1), 6), round(y / (height - 1), 6)]
                        for x, y in polygons[1]
                    ]
                    warning_points = tuple(tuple(point) for point in warning_zone)
                    if not all(
                        point_in_polygon(tuple(point), warning_points)
                        for point in danger_zone
                    ):
                        raise ValueError(
                            "Danger-zone vertices must all be inside the warning zone"
                        )
                    raw["risk"]["warning_zone"] = warning_zone
                    raw["risk"]["danger_zone"] = danger_zone
                    raw.setdefault("machine", {})["motion_roi"] = [
                        [round(x / (width - 1), 6), round(y / (height - 1), 6)]
                        for x, y in polygons[2]
                    ]
                    raw["machine"]["reference_roi"] = [
                        [round(x / (width - 1), 6), round(y / (height - 1), 6)]
                        for x, y in polygons[3]
                    ]
                    config_path.write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    load_config(config_path)
                    if manifest is not None and station_spec is not None:
                        update_manifest_station(
                            manifest.path,
                            station_spec.station_id,
                            zones_calibrated=True,
                        )
                    print(f"Saved calibrated zones to {config_path}")
                    return 0
    finally:
        capture.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
