"""Overlay for the joint hand-pose-machine research prototype."""

from __future__ import annotations

from collections import deque
import math

import cv2
import numpy as np

from .config import StationConfig
from .geometry import Point, normalized_to_pixels
from .joint_engine import JointAssessment, JointState
from .machine_motion import MachineObservation
from .pose_tracker import PoseObservation, UPPER_BODY_CONNECTIONS

COLORS = {
    JointState.NORMAL: (72, 179, 73),
    JointState.ATTENTION: (0, 210, 255),
    JointState.WARNING: (0, 140, 255),
    JointState.DANGER: (40, 40, 235),
    JointState.UNCERTAIN: (185, 90, 180),
    JointState.FAULT: (90, 90, 90),
}

HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)


class TrajectoryBuffer:
    def __init__(self, max_points: int = 46):
        self._tracks = [deque(maxlen=max_points), deque(maxlen=max_points)]
        self._missing = [0, 0]

    def update(self, hands: list[list[Point]]) -> list[list[Point]]:
        centers = [
            (
                sum(point[0] for point in hand) / len(hand),
                sum(point[1] for point in hand) / len(hand),
            )
            for hand in hands[:2]
            if hand
        ]
        assignments: dict[int, Point] = {}
        if len(centers) == 1:
            candidates = [
                (self._distance(track[-1], centers[0]) if track else float(index), index)
                for index, track in enumerate(self._tracks)
            ]
            assignments[min(candidates)[1]] = centers[0]
        elif len(centers) >= 2:
            direct = self._assignment_cost((centers[0], centers[1]))
            swapped = self._assignment_cost((centers[1], centers[0]))
            ordered = (centers[0], centers[1]) if direct <= swapped else (centers[1], centers[0])
            assignments = {0: ordered[0], 1: ordered[1]}

        for index, track in enumerate(self._tracks):
            center = assignments.get(index)
            if center is None:
                self._missing[index] += 1
                if self._missing[index] > 5:
                    track.clear()
                continue
            if track and self._distance(track[-1], center) > 0.16:
                track.clear()
            track.append(center)
            self._missing[index] = 0
        return [list(track) for track in self._tracks]

    def _assignment_cost(self, ordered: tuple[Point, Point]) -> float:
        return sum(
            self._distance(track[-1], center) if track else 0.0
            for track, center in zip(self._tracks, ordered)
        )

    @staticmethod
    def _distance(first: Point, second: Point) -> float:
        return math.hypot(first[0] - second[0], first[1] - second[1])


def draw_joint_overlay(
    frame: np.ndarray,
    config: StationConfig,
    assessment: JointAssessment,
    hands: list[list[Point]],
    pose: PoseObservation | None,
    machine: MachineObservation,
    trajectories: list[list[Point]],
    *,
    draw_risk_zones: bool = True,
    draw_machine_zone: bool = True,
    draw_pose: bool = True,
    draw_hands: bool = True,
    draw_panel: bool = True,
    draw_legend: bool = True,
) -> np.ndarray:
    output = frame.copy()
    height, width = output.shape[:2]
    if draw_risk_zones:
        _draw_zone(output, config.risk.warning_zone, (0, 196, 255), 0.10)
        _draw_zone(output, config.risk.danger_zone, (40, 40, 235), 0.13)
    if draw_machine_zone:
        _draw_zone(output, config.machine.motion_roi, (230, 120, 45), 0.05)
    if draw_pose:
        _draw_pose(output, pose)
    if draw_hands:
        _draw_hands(output, hands)
        for track in trajectories:
            points = normalized_to_pixels(track, width, height)
            if len(points) >= 2:
                cv2.polylines(output, [np.array(points, np.int32)], False, (255, 210, 90), 2, cv2.LINE_AA)
    if draw_panel:
        _status_panel(output, assessment, machine)
    if draw_legend:
        _zone_legend(output)
    if draw_panel and assessment.state in (JointState.UNCERTAIN, JointState.FAULT):
        message = (
            "SYSTEM UNAVAILABLE"
            if assessment.state == JointState.FAULT
            else "JUDGMENT UNCERTAIN - DO NOT ASSUME SAFE"
        )
        cv2.rectangle(output, (18, 200), (min(width - 18, 675), 242), (38, 38, 44), -1)
        cv2.putText(
            output,
            message,
            (34, 229),
            cv2.FONT_HERSHEY_DUPLEX,
            0.66,
            COLORS[assessment.state],
            2,
            cv2.LINE_AA,
        )
    cv2.putText(
        output,
        "RESEARCH PROTOTYPE - NO MACHINE CONTROL",
        (20, height - 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (245, 245, 245),
        2,
        cv2.LINE_AA,
    )
    return output


def _draw_zone(
    frame: np.ndarray,
    polygon: tuple[Point, ...],
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    height, width = frame.shape[:2]
    points = np.array(normalized_to_pixels(polygon, width, height), np.int32)
    tint = frame.copy()
    cv2.fillPoly(tint, [points], color)
    cv2.addWeighted(tint, alpha, frame, 1.0 - alpha, 0, frame)
    cv2.polylines(frame, [points], True, color, 2, cv2.LINE_AA)


def _zone_legend(frame: np.ndarray) -> None:
    """Keep labels off the monitored surface and attach meaning in one legend."""
    height, width = frame.shape[:2]
    x0 = 18
    y0 = max(250, height - 84)
    legend_width = min(820, width - 36)
    cv2.rectangle(frame, (x0, y0), (x0 + legend_width, y0 + 42), (24, 27, 33), -1)
    entries = (
        ((0, 196, 255), "ATTENTION BUFFER"),
        ((40, 40, 235), "DANGER GAP"),
        ((230, 120, 45), "MACHINE ROI - UNVERIFIED"),
    )
    cursor = x0 + 16
    for color, label in entries:
        cv2.rectangle(frame, (cursor, y0 + 12), (cursor + 28, y0 + 29), color, -1)
        cursor += 38
        cv2.putText(
            frame,
            label,
            (cursor, y0 + 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (235, 238, 242),
            1,
            cv2.LINE_AA,
        )
        cursor += 178 if "MACHINE" not in label else 252


def _draw_pose(frame: np.ndarray, pose: PoseObservation | None) -> None:
    if pose is None:
        return
    height, width = frame.shape[:2]
    for first, second in UPPER_BODY_CONNECTIONS:
        a, b = pose.landmarks[first], pose.landmarks[second]
        if min(a.visibility, b.visibility) < 0.3:
            continue
        start = (int(a.x * width), int(a.y * height))
        end = (int(b.x * width), int(b.y * height))
        cv2.line(frame, start, end, (100, 230, 130), 3, cv2.LINE_AA)
    for index in {point for pair in UPPER_BODY_CONNECTIONS for point in pair}:
        point = pose.landmarks[index]
        if point.visibility >= 0.3:
            cv2.circle(frame, (int(point.x * width), int(point.y * height)), 5, (90, 245, 140), -1, cv2.LINE_AA)
    if pose.torso_center is not None:
        center = (int(pose.torso_center[0] * width), int(pose.torso_center[1] * height))
        cv2.drawMarker(frame, center, (255, 210, 60), cv2.MARKER_CROSS, 18, 3)


def _draw_hands(frame: np.ndarray, hands: list[list[Point]]) -> None:
    height, width = frame.shape[:2]
    for hand in hands:
        for first, second in HAND_CONNECTIONS:
            if max(first, second) >= len(hand):
                continue
            a, b = hand[first], hand[second]
            cv2.line(
                frame,
                (int(a[0] * width), int(a[1] * height)),
                (int(b[0] * width), int(b[1] * height)),
                (255, 190, 80),
                1,
                cv2.LINE_AA,
            )
        for x, y in hand:
            cv2.circle(frame, (int(x * width), int(y * height)), 3, (255, 225, 110), -1, cv2.LINE_AA)


def _status_panel(frame: np.ndarray, assessment: JointAssessment, machine: MachineObservation) -> None:
    color = COLORS[assessment.state]
    height, width = frame.shape[:2]
    panel_width = min(570, width - 36)
    cv2.rectangle(frame, (18, 18), (18 + panel_width, 190), (24, 27, 33), -1)
    cv2.rectangle(frame, (18, 18), (18 + panel_width, 190), color, 3)
    cv2.putText(frame, assessment.state.name, (38, 60), cv2.FONT_HERSHEY_DUPLEX, 1.0, color, 2, cv2.LINE_AA)
    rows = [
        f"Reason: {assessment.reason.replace('_', ' ')}",
        f"Hands {assessment.hand_count} | conf {assessment.hand_confidence:.2f} | clearance {_fmt(assessment.min_hand_clearance)} | dwell {assessment.danger_dwell_s:.2f}s",
        f"Machine {machine.state.value} | visual {machine.raw_state.value} | {machine.reason.replace('_', ' ')} | flow {machine.vertical_flow:+.2f}",
        f"Pose vis {assessment.pose_visibility:.2f} | lean {_fmt(assessment.torso_lean_deg)} deg | elbows {_fmt(assessment.left_elbow_angle)}/{_fmt(assessment.right_elbow_angle)}",
    ]
    for index, row in enumerate(rows):
        cv2.putText(frame, row[:78], (38, 92 + index * 27), cv2.FONT_HERSHEY_SIMPLEX, 0.47, (225, 225, 225), 1, cv2.LINE_AA)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
