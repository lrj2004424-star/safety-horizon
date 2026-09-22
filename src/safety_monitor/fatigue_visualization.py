"""Readable overlay for anonymous whole-person fatigue-risk observation."""

from __future__ import annotations

import cv2
import numpy as np

from .fatigue_engine import FatigueAssessment, FatigueState, PersonFatigueAssessment
from .pose_tracker import PoseObservation


COLORS = {
    FatigueState.OBSERVING: (240, 165, 65),
    FatigueState.ACTIVE: (70, 185, 85),
    FatigueState.FATIGUE_TREND: (0, 155, 255),
    FatigueState.HIGH_RISK: (45, 45, 235),
    FatigueState.UNCERTAIN: (185, 90, 180),
}

STATE_LABELS = {
    FatigueState.OBSERVING: "OBSERVING BASELINE",
    FatigueState.ACTIVE: "WORK PATTERN ACTIVE",
    FatigueState.FATIGUE_TREND: "FATIGUE TREND",
    FatigueState.HIGH_RISK: "HIGH FATIGUE RISK",
    FatigueState.UNCERTAIN: "VIEW UNCERTAIN",
}

BODY_CONNECTIONS = (
    (0, 11), (0, 12),
    (11, 12),
    (11, 13), (13, 15),
    (12, 14), (14, 16),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27),
    (24, 26), (26, 28),
)

# A candidate is visible evidence returned by MediaPipe but not yet accepted by
# the stricter temporal risk engine.  Keep it visually distinct from every risk
# state so an operator never mistakes "landmarks found" for "danger confirmed".
CANDIDATE_COLOR = (235, 190, 55)


def draw_fatigue_overlay(
    frame: np.ndarray,
    assessment: FatigueAssessment,
    *,
    recording: bool = False,
    recording_mode: str | None = None,
) -> np.ndarray:
    output = frame.copy()
    height, width = output.shape[:2]
    for person in assessment.persons:
        _draw_person(output, person)

    color = COLORS[assessment.state]
    panel_width = min(width - 28, 660)
    cv2.rectangle(output, (14, 14), (14 + panel_width, 133), (24, 27, 33), -1)
    cv2.rectangle(output, (14, 14), (23, 133), color, -1)
    cv2.putText(
        output,
        STATE_LABELS[assessment.state],
        (38, 52),
        cv2.FONT_HERSHEY_DUPLEX,
        0.82,
        color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        output,
        f"Overall risk score: {assessment.score:3d}/100   People visible: {len(assessment.persons)}",
        (38, 82),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (235, 235, 235),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        output,
        "Personal baseline + persistent posture/motion change",
        (38, 108),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.49,
        (200, 205, 214),
        1,
        cv2.LINE_AA,
    )

    controls = "S SNAPSHOT   R STOP RECORDING   Q QUIT" if recording else "S SNAPSHOT   R START RECORDING   Q QUIT"
    if recording:
        controls = f"REC {str(recording_mode or '').upper()}   " + controls
    footer_width = min(width - 28, 690)
    cv2.rectangle(output, (14, height - 72), (14 + footer_width, height - 14), (24, 27, 33), -1)
    cv2.putText(output, controls, (30, height - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (245, 245, 245), 1, cv2.LINE_AA)
    cv2.putText(output, "ADVISORY PROXY - NOT A MEDICAL DIAGNOSIS", (30, height - 23), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (190, 195, 205), 1, cv2.LINE_AA)
    return output


def draw_station_fatigue_overlay(
    frame: np.ndarray,
    assessment: FatigueAssessment,
    station_label: str,
    candidate_poses: tuple[PoseObservation, ...] = (),
) -> np.ndarray:
    """Compact tile overlay for one crop in a multi-station dashboard."""
    body = frame.copy()
    height, width = body.shape[:2]
    for person in assessment.persons:
        _draw_person(body, person, top_margin=8, bottom_margin=34)
    if not assessment.persons:
        for pose in candidate_poses:
            _draw_candidate_pose(body, pose, top_margin=8, bottom_margin=34)
    color = COLORS[assessment.state]
    header_height = 104
    output = np.full((height + header_height, width, 3), (24, 27, 33), dtype=np.uint8)
    output[header_height:, :] = body
    cv2.rectangle(output, (0, 0), (10, header_height), color, -1)
    cv2.putText(
        output,
        f"{station_label}  |  {STATE_LABELS[assessment.state]}",
        (24, 35),
        cv2.FONT_HERSHEY_DUPLEX,
        0.64,
        color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        output,
        f"Risk {assessment.score:3d}/100   Visible people {len(assessment.persons)}",
        (24, 65),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (238, 238, 238),
        1,
        cv2.LINE_AA,
    )
    reason = assessment.reason.replace("_", " ")
    cv2.putText(
        output,
        reason[:72],
        (24, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.40,
        (200, 205, 214),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        output,
        "INDEPENDENT WHOLE-PERSON BASELINE",
        (18, output.shape[0] - 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        color,
        1,
        cv2.LINE_AA,
    )
    return output


def draw_station_pose_overlay(
    frame: np.ndarray,
    assessment: FatigueAssessment,
    candidate_poses: tuple[PoseObservation, ...] = (),
) -> np.ndarray:
    """Draw only spatial evidence; numerical evidence belongs beside video.

    The daily dashboard uses this clean view so camera pixels are not covered
    by a large status header.  The detailed workflow preview still uses
    :func:`draw_station_fatigue_overlay` and therefore remains independently
    inspectable inside TouchDesigner.
    """
    output = frame.copy()
    for person in assessment.persons:
        _draw_person(
            output,
            person,
            top_margin=8,
            bottom_margin=8,
            compact=True,
        )
    if not assessment.persons:
        for pose in candidate_poses:
            _draw_candidate_pose(
                output,
                pose,
                top_margin=8,
                bottom_margin=8,
                compact=True,
            )
    return output


def _draw_candidate_pose(
    frame: np.ndarray,
    pose: PoseObservation,
    *,
    top_margin: int,
    bottom_margin: int,
    compact: bool = False,
) -> None:
    """Draw raw pose evidence without promoting it to a risk assessment."""
    height, width = frame.shape[:2]
    points = pose.landmarks
    threshold = 0.12
    for first, second in BODY_CONNECTIONS:
        a, b = points[first], points[second]
        if min(a.visibility, b.visibility, a.presence, b.presence) < threshold:
            continue
        cv2.line(
            frame,
            (int(a.x * width), int(a.y * height)),
            (int(b.x * width), int(b.y * height)),
            CANDIDATE_COLOR,
            2,
            cv2.LINE_AA,
        )
    visible = [
        points[index]
        for index in (0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28)
        if min(points[index].visibility, points[index].presence) >= threshold
        and -0.10 <= points[index].x <= 1.10
        and -0.10 <= points[index].y <= 1.10
    ]
    if len(visible) < 4:
        return
    x_values = [int(point.x * width) for point in visible]
    y_values = [int(point.y * height) for point in visible]
    left, right = max(0, min(x_values) - 18), min(width - 1, max(x_values) + 18)
    top = max(top_margin, min(y_values) - 28)
    bottom = min(height - bottom_margin, max(y_values) + 18)
    if right <= left or bottom <= top:
        return
    cv2.rectangle(frame, (left, top), (right, bottom), CANDIDATE_COLOR, 2, cv2.LINE_AA)
    label = "POSE CANDIDATE" if compact else "POSE CANDIDATE - RISK UNCONFIRMED"
    cv2.putText(
        frame,
        label,
        (left, max(top_margin + 14, top - 7)),
        cv2.FONT_HERSHEY_DUPLEX,
        0.40 if compact else 0.43,
        CANDIDATE_COLOR,
        1,
        cv2.LINE_AA,
    )


def _draw_person(
    frame: np.ndarray,
    person: PersonFatigueAssessment,
    *,
    top_margin: int = 142,
    bottom_margin: int = 82,
    compact: bool = False,
) -> None:
    height, width = frame.shape[:2]
    color = COLORS[person.state]
    points = person.pose.landmarks
    for first, second in BODY_CONNECTIONS:
        a, b = points[first], points[second]
        if min(a.visibility, b.visibility) < 0.35:
            continue
        cv2.line(
            frame,
            (int(a.x * width), int(a.y * height)),
            (int(b.x * width), int(b.y * height)),
            color,
            3,
            cv2.LINE_AA,
        )
    visible = [points[index] for index in (0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28) if points[index].visibility >= 0.35]
    if not visible:
        return
    x_values = [int(point.x * width) for point in visible]
    y_values = [int(point.y * height) for point in visible]
    left, right = max(0, min(x_values) - 22), min(width - 1, max(x_values) + 22)
    top = max(top_margin, min(y_values) - 34)
    bottom = min(height - bottom_margin, max(y_values) + 20)
    if right > left and bottom > top:
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2, cv2.LINE_AA)
        label = (
            f"P{person.track_id}"
            if compact
            else f"P{person.track_id}  {STATE_LABELS[person.state]}  {person.score}/100"
        )
        label_y = max(top_margin + 15, top - 8)
        cv2.putText(frame, label, (left, label_y), cv2.FONT_HERSHEY_DUPLEX, 0.46, color, 2, cv2.LINE_AA)
        if not compact:
            detail = f"head {person.head_drop_change:+.2f}  lean {person.lean_change_deg:.0f}deg  motion {person.motion_rate:.3f}/s"
            cv2.putText(frame, detail, (left, min(height - bottom_margin - 4, bottom + 19)), cv2.FONT_HERSHEY_SIMPLEX, 0.39, (240, 240, 240), 1, cv2.LINE_AA)
