#!/usr/bin/env python3
"""Select one independent rectangular processing crop per wide-camera workstation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import cv2

from safety_monitor.camera_device import CameraDevice, redact_source
from safety_monitor.config import load_config
from safety_monitor.wide_station import (
    crop_bounds,
    load_wide_manifest,
    update_manifest_station,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "config" / "video32-wide.example.json"),
    )
    parser.add_argument("--source", help="Camera index, video/image path, or RTSP/HTTP URL")
    parser.add_argument("--source-env", help="Environment variable containing the stream URL")
    return parser.parse_args()


def _source_value(raw: str | None, default: int) -> int | str:
    if raw is None:
        return default
    return int(raw) if raw.isdigit() else raw


def main() -> int:
    args = parse_args()
    manifest = load_wide_manifest(args.manifest)
    first_config = load_config(manifest.stations[0].config_path)
    if args.source is not None and args.source_env is not None:
        raise ValueError("Use either --source or --source-env, not both")
    raw = os.environ.get(args.source_env) if args.source_env else args.source
    if args.source_env and not raw:
        raise ValueError(f"Environment variable {args.source_env!r} is missing or empty")
    source = _source_value(raw, first_config.camera_index)
    camera = CameraDevice(
        source,
        width=first_config.camera_width,
        height=first_config.camera_height,
        fps=first_config.camera_fps,
    )
    if not camera.is_opened():
        raise RuntimeError(f"Could not open source: {redact_source(source)}")
    try:
        read = camera.read_observation()
        if not read.ok or read.frame is None:
            raise RuntimeError(read.reason)
        frame = cv2.flip(read.frame, 1) if first_config.mirror else read.frame
    finally:
        camera.release()

    source_height, source_width = frame.shape[:2]
    max_width, max_height = 1600, 900
    scale = min(1.0, max_width / source_width, max_height / source_height)
    preview_width = max(1, int(round(source_width * scale)))
    preview_height = max(1, int(round(source_height * scale)))
    base_preview = cv2.resize(
        frame,
        (preview_width, preview_height),
        interpolation=cv2.INTER_AREA,
    )
    selected: dict[str, tuple[float, float, float, float]] = {}
    for index, station in enumerate(manifest.stations):
        preview = base_preview.copy()
        for prior in manifest.stations[:index]:
            crop = selected.get(prior.station_id, prior.crop)
            left, top, right, bottom = crop_bounds(
                crop, preview_width, preview_height
            )
            cv2.rectangle(preview, (left, top), (right, bottom), (80, 220, 120), 2)
            cv2.putText(
                preview,
                prior.station_id,
                (left + 8, max(24, top + 24)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (80, 220, 120),
                2,
                cv2.LINE_AA,
            )
        cv2.rectangle(preview, (12, 12), (min(preview_width - 12, 900), 62), (24, 27, 33), -1)
        cv2.putText(
            preview,
            f"SELECT {station.station_id}: drag rectangle, then Enter/Space; C cancels",
            (28, 45),
            cv2.FONT_HERSHEY_DUPLEX,
            0.62,
            (0, 210, 255),
            2,
            cv2.LINE_AA,
        )
        x, y, width, height = cv2.selectROI(
            f"Wide layout - {station.station_id}",
            preview,
            showCrosshair=True,
            fromCenter=False,
        )
        cv2.destroyWindow(f"Wide layout - {station.station_id}")
        if width <= 1 or height <= 1:
            print("Layout calibration cancelled; manifest was not changed.")
            cv2.destroyAllWindows()
            return 1
        selected[station.station_id] = (
            x / preview_width,
            y / preview_height,
            (x + width) / preview_width,
            (y + height) / preview_height,
        )

    for station_id, crop in selected.items():
        update_manifest_station(
            manifest.path,
            station_id,
            crop=crop,
            crop_verified=True,
            zones_calibrated=False,
        )
    cv2.destroyAllWindows()
    print(f"Saved verified station crops to {manifest.path}")
    print("Next: run calibrate.py with --wide-manifest and --station-id for each station.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
