#!/usr/bin/env python3
"""Explicitly audition one or all alert cues before a monitored session."""

from __future__ import annotations

import argparse
import time
from dataclasses import replace
from pathlib import Path

from safety_monitor import load_config
from safety_monitor.audio_alerts import AlertSoundManager
from safety_monitor.joint_engine import JointState

PROJECT_ROOT = Path(__file__).resolve().parent
STATES = {
    "attention": JointState.ATTENTION,
    "warning": JointState.WARNING,
    "danger": JointState.DANGER,
    "uncertain": JointState.UNCERTAIN,
    "fault": JointState.FAULT,
}
REASONS = {
    "uncertain": "hand_or_upper_body_not_reliably_visible",
    "fault": "camera_stream_fault",
}
WAIT_SECONDS = {
    "attention": 2.2,
    "warning": 4.7,
    "danger": 9.2,
    "uncertain": 4.5,
    "fault": 4.5,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "station.example.json"))
    parser.add_argument("--state", choices=("all", *STATES), default="all")
    parser.add_argument("--volume", type=float, help="Temporary 0..1 preview volume")
    args = parser.parse_args()
    station = load_config(Path(args.config).expanduser().resolve())
    audio_config = station.audio_alerts
    if args.volume is not None:
        if not 0.0 <= args.volume <= 1.0:
            raise ValueError("--volume must be between 0 and 1")
        audio_config = replace(audio_config, volume=args.volume)
    assets = Path(audio_config.assets_dir).expanduser()
    if not assets.is_absolute():
        assets = (PROJECT_ROOT / assets).resolve()
    manager = AlertSoundManager(replace(audio_config, startup_grace_s=0.0), assets)
    if not manager.available:
        print(f"Audio check unavailable: {manager.status}")
        return 2
    selected = STATES.items() if args.state == "all" else ((args.state, STATES[args.state]),)
    timestamp = 1.0
    try:
        print("NORMAL: silent by design")
        for name, state in selected:
            update = manager.update(
                state,
                timestamp,
                reason=REASONS.get(name),
            )
            detail = update.message or update.asset or update.reason
            print(f"{name.upper()}: {'playing' if update.played else update.reason} | {detail}")
            wait_s = WAIT_SECONDS[name]
            timestamp += wait_s
            time.sleep(wait_s)
    finally:
        manager.close(stop_active=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
