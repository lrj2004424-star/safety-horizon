"""OpenCV overlay for risk zones, detected landmarks, and advisory status."""

from __future__ import annotations

import cv2
import numpy as np

from .config import RiskConfig
from .geometry import Point, normalized_to_pixels
from .risk_engine import RiskAssessment, RiskState

STATE_COLORS = {
    RiskState.CLEAR: (72, 179, 73),
    RiskState.WARNING: (0, 196, 255),
    RiskState.DANGER: (48, 48, 235),
}


def draw_overlay(
    frame: np.ndarray,
    config: RiskConfig,
    assessment: RiskAssessment,
    hands: list[list[Point]],
) -> np.ndarray:
    output = frame.copy()
    height, width = output.shape[:2]
    warning = np.array(
        normalized_to_pixels(config.warning_zone, width, height), np.int32
    )
    danger = np.array(
        normalized_to_pixels(config.danger_zone, width, height), np.int32
    )

    tint = output.copy()
    cv2.fillPoly(tint, [warning], (0, 196, 255))
    cv2.fillPoly(tint, [danger], (48, 48, 235))
    cv2.addWeighted(tint, 0.13, output, 0.87, 0, output)
    cv2.polylines(output, [warning], True, (0, 196, 255), 2, cv2.LINE_AA)
    cv2.polylines(output, [danger], True, (48, 48, 235), 3, cv2.LINE_AA)

    for hand in hands:
        for x, y in hand:
            point = (int(x * (width - 1)), int(y * (height - 1)))
            cv2.circle(output, point, 3, (255, 230, 120), -1, cv2.LINE_AA)

    _draw_status_panel(output, assessment)
    cv2.putText(
        output,
        "WARNING ZONE",
        tuple(warning[0]),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 196, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        output,
        "DANGER ZONE",
        tuple(danger[0]),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (48, 48, 235),
        2,
        cv2.LINE_AA,
    )
    return output


def _draw_status_panel(frame: np.ndarray, assessment: RiskAssessment) -> None:
    color = STATE_COLORS[assessment.state]
    cv2.rectangle(frame, (18, 18), (450, 148), (25, 28, 34), -1)
    cv2.rectangle(frame, (18, 18), (450, 148), color, 3)
    cv2.putText(
        frame,
        assessment.state.name,
        (38, 62),
        cv2.FONT_HERSHEY_DUPLEX,
        1.05,
        color,
        2,
        cv2.LINE_AA,
    )
    detection_text = (
        f"Hands: {assessment.hand_count}"
        if assessment.hand_count
        else "No hand detected - not a safety guarantee"
    )
    cv2.putText(
        frame,
        detection_text,
        (38, 94),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (225, 225, 225),
        1,
        cv2.LINE_AA,
    )
    detail = assessment.reason.replace("_", " ")
    if assessment.predicted_entry_s is not None:
        detail += f" | ETA {assessment.predicted_entry_s:.2f}s"
    cv2.putText(
        frame,
        detail[:62],
        (38, 124),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.46,
        (205, 205, 205),
        1,
        cv2.LINE_AA,
    )
