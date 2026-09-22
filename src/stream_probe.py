#!/usr/bin/env python3
"""Probe a live network stream without saving frames or printing credentials."""

from __future__ import annotations

import argparse
import json
import os
import time

import cv2

from safety_monitor.camera_device import CameraDevice, is_network_stream_source, redact_source


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", help="RTSP/HTTP URL; prefer --source-env when it contains credentials")
    source.add_argument("--source-env", help="Environment variable containing the RTSP/HTTP URL")
    parser.add_argument("--frames", type=int, default=30, help="Frames to read before reporting health")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw = os.environ.get(args.source_env) if args.source_env else args.source
    if not raw:
        raise ValueError(f"Environment variable {args.source_env!r} is missing or empty")
    if not is_network_stream_source(raw):
        raise ValueError("stream_probe accepts RTSP/HTTP network streams only")
    requested = max(1, int(args.frames))
    started = time.monotonic()
    first_frame_s: float | None = None
    successful = 0
    device = CameraDevice(raw, reconnect_attempts=2)
    try:
        if not device.is_opened():
            print(
                json.dumps(
                    {"ok": False, "source": redact_source(raw), "reason": "stream_open_failed"},
                    ensure_ascii=False,
                )
            )
            return 2
        for _ in range(requested):
            observation = device.read_observation()
            if not observation.ok or observation.frame is None:
                break
            successful += 1
            if first_frame_s is None:
                first_frame_s = time.monotonic() - started
        elapsed = max(time.monotonic() - started, 1e-9)
        width = int(device.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(device.get(cv2.CAP_PROP_FRAME_HEIGHT))
        reported_fps = float(device.get(cv2.CAP_PROP_FPS))
        result = {
            "ok": successful == requested,
            "source": redact_source(raw),
            "frames_requested": requested,
            "frames_read": successful,
            "width": width,
            "height": height,
            "reported_fps": round(reported_fps, 3),
            "observed_read_fps": round(successful / elapsed, 3),
            "first_frame_s": round(first_frame_s, 3) if first_frame_s is not None else None,
            "reconnects": device.reconnect_count,
            "saved_frames": 0,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    finally:
        device.release()


if __name__ == "__main__":
    raise SystemExit(main())
