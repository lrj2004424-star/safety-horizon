#!/usr/bin/env python3
"""Generate a four-state proof image without a camera or personal data."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from safety_monitor import RiskEngine, RiskState, load_config
from safety_monitor.geometry import Point
from safety_monitor.visualization import draw_overlay

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "station.example.json"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "artifacts" / "four-state-simulation.png"),
    )
    return parser.parse_args()


def synthetic_hand(center: Point) -> list[Point]:
    cx, cy = center
    offsets = [
        (0.00, 0.05), (-0.015, 0.025), (-0.025, 0.00), (-0.035, -0.025),
        (-0.045, -0.05), (-0.012, 0.00), (-0.012, -0.035), (-0.012, -0.07),
        (-0.012, -0.10), (0.00, -0.005), (0.00, -0.045), (0.00, -0.085),
        (0.00, -0.12), (0.013, 0.00), (0.016, -0.035), (0.019, -0.068),
        (0.022, -0.10), (0.026, 0.012), (0.033, -0.018), (0.040, -0.045),
        (0.047, -0.07),
    ]
    return [
        (max(0.0, min(1.0, cx + dx)), max(0.0, min(1.0, cy + dy)))
        for dx, dy in offsets
    ]


def background(width: int, height: int) -> np.ndarray:
    image = np.full((height, width, 3), (55, 58, 62), dtype=np.uint8)
    cv2.rectangle(image, (0, int(height * 0.3)), (width, height), (82, 86, 90), -1)
    for x in range(0, width, 90):
        cv2.line(image, (x, int(height * 0.3)), (x + 120, height), (67, 70, 74), 1)
    cv2.putText(
        image,
        "SYNTHETIC CUTTING-STATION FEED - NO WORKER VIDEO",
        (22, height - 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (210, 210, 210),
        1,
        cv2.LINE_AA,
    )
    return image


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    engine = RiskEngine(config.risk)
    width, height = 960, 540
    snapshots: list[tuple[str, np.ndarray]] = []
    captured_states: set[RiskState] = set()
    has_cleared_snapshot = False

    path = (
        [(0.12 + index * 0.006, 0.58) for index in range(95)]
        + [(0.69 + index * 0.006, 0.58) for index in range(35)]
    )
    for index, center in enumerate(path):
        hand = synthetic_hand(center)
        assessment = engine.update([hand], index / 30.0)
        frame = draw_overlay(background(width, height), config.risk, assessment, [hand])
        if assessment.state not in captured_states:
            snapshots.append((assessment.state.name, frame))
            captured_states.add(assessment.state)
        elif (
            assessment.state == RiskState.CLEAR
            and RiskState.DANGER in captured_states
            and not has_cleared_snapshot
        ):
            snapshots.append(("RISK CLEARED", frame))
            has_cleared_snapshot = True
        if len(snapshots) >= 4:
            break

    if len(snapshots) != 4:
        raise RuntimeError(f"Expected four state snapshots, got {len(snapshots)}")

    panels: list[np.ndarray] = []
    for title, image in snapshots:
        panel = image.copy()
        cv2.rectangle(panel, (width - 260, 14), (width - 14, 58), (14, 16, 20), -1)
        cv2.putText(
            panel,
            title,
            (width - 238, 45),
            cv2.FONT_HERSHEY_DUPLEX,
            0.8,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        panels.append(panel)
    contact_sheet = cv2.vconcat(
        [cv2.hconcat(panels[:2]), cv2.hconcat(panels[2:])]
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), contact_sheet):
        raise RuntimeError(f"Could not write {output}")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
