"""Continuous hand, upper-body and machine-state fusion for the research MVP."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from .config import RiskConfig
from .geometry import Point, signed_distance_to_polygon
from .machine_motion import MachineObservation, MachineState
from .pose_tracker import PoseObservation


class JointState(IntEnum):
    NORMAL = 0
    ATTENTION = 1
    WARNING = 2
    DANGER = 3
    UNCERTAIN = 4
    FAULT = 5


@dataclass(frozen=True)
class JointAssessment:
    state: JointState
    raw_state: JointState
    reason: str
    transition: tuple[JointState, JointState] | None
    hand_count: int
    hand_confidence: float
    min_hand_clearance: float | None
    hand_approach_speed: float
    predicted_entry_s: float | None
    danger_dwell_s: float
    both_hands_withdrawn: bool
    left_elbow_angle: float | None
    right_elbow_angle: float | None
    elbow_change_deg: float | None
    torso_lean_deg: float | None
    torso_lean_change_deg: float | None
    torso_approach_speed: float
    pose_visibility: float
    pose_upper_body_visible: bool
    machine_state: MachineState
    machine_raw_state: MachineState
    machine_vertical_flow: float
    machine_moving_fraction: float
    machine_reference_motion: float
    machine_quality_ok: bool

    def feature_dict(self) -> dict[str, float | int | str | bool | None]:
        return {
            "state": self.state.name,
            "raw_state": self.raw_state.name,
            "reason": self.reason,
            "hand_count": self.hand_count,
            "hand_confidence": _rounded(self.hand_confidence),
            "min_hand_clearance": _rounded(self.min_hand_clearance),
            "hand_approach_speed": _rounded(self.hand_approach_speed),
            "predicted_entry_s": _rounded(self.predicted_entry_s),
            "danger_dwell_s": _rounded(self.danger_dwell_s),
            "both_hands_withdrawn": self.both_hands_withdrawn,
            "left_elbow_angle": _rounded(self.left_elbow_angle),
            "right_elbow_angle": _rounded(self.right_elbow_angle),
            "elbow_change_deg": _rounded(self.elbow_change_deg),
            "torso_lean_deg": _rounded(self.torso_lean_deg),
            "torso_lean_change_deg": _rounded(self.torso_lean_change_deg),
            "torso_approach_speed": _rounded(self.torso_approach_speed),
            "pose_visibility": _rounded(self.pose_visibility),
            "pose_upper_body_visible": self.pose_upper_body_visible,
            "machine_state": self.machine_state.value,
            "machine_raw_state": self.machine_raw_state.value,
            "machine_vertical_flow": _rounded(self.machine_vertical_flow),
            "machine_moving_fraction": _rounded(self.machine_moving_fraction),
            "machine_reference_motion": _rounded(self.machine_reference_motion),
            "machine_quality_ok": self.machine_quality_ok,
        }


class JointRiskEngine:
    """Interpretable temporal baseline; not a safety-certified controller."""

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = JointState.UNCERTAIN
        self._candidate: JointState | None = None
        self._candidate_frames = 0
        self._previous_clearance: float | None = None
        self._previous_torso: Point | None = None
        self._previous_hand_timestamp: float | None = None
        self._previous_torso_timestamp: float | None = None
        self._previous_elbows: tuple[float | None, float | None] = (None, None)
        self._last_elbow_change: float | None = None
        self._last_torso_speed = 0.0
        self._baseline_lean: float | None = None
        self._smoothed_speed = 0.0
        self._danger_entered_at: float | None = None
        self._last_hand_seen_at: float | None = None
        self._last_pose_seen_at: float | None = None

    def update(
        self,
        hands: list[list[Point]],
        pose: PoseObservation | None,
        machine: MachineObservation,
        timestamp_s: float,
        *,
        stream_ok: bool = True,
        hand_confidence: float = 1.0,
        quality_issue: str | None = None,
        pose_updated: bool = True,
    ) -> JointAssessment:
        points = [
            hand[index]
            for hand in hands
            for index in self.config.relevant_landmarks
            if index < len(hand)
        ]
        if points and hand_confidence >= self.config.min_hand_confidence:
            self._last_hand_seen_at = timestamp_s
        if pose_updated and pose is not None and pose.upper_body_visible:
            self._last_pose_seen_at = timestamp_s

        min_clearance = (
            min(signed_distance_to_polygon(point, self.config.danger_zone) for point in points)
            if points else None
        )
        warning_clearance = (
            min(signed_distance_to_polygon(point, self.config.warning_zone) for point in points)
            if points else None
        )
        approach_speed = self._hand_speed(min_clearance, timestamp_s)
        predicted_entry = (
            min_clearance / approach_speed
            if min_clearance is not None
            and min_clearance > 0
            and approach_speed >= self.config.approach_speed_threshold
            else None
        )
        in_danger = min_clearance is not None and min_clearance <= 0
        if in_danger and self._danger_entered_at is None:
            self._danger_entered_at = timestamp_s
        elif not in_danger:
            self._danger_entered_at = None
        danger_dwell = (
            max(0.0, timestamp_s - self._danger_entered_at)
            if self._danger_entered_at is not None else 0.0
        )
        both_withdrawn = len(hands) >= 2 and all(
            all(signed_distance_to_polygon(point, self.config.warning_zone) > 0 for point in hand)
            for hand in hands
        )

        pose_visibility = pose.visibility_score if pose is not None else 0.0
        lean = pose.torso_lean_deg if pose is not None else None
        if lean is not None and self._baseline_lean is None:
            self._baseline_lean = lean
        lean_change = (
            abs(lean - self._baseline_lean)
            if lean is not None and self._baseline_lean is not None else None
        )
        elbow_change = self._elbow_change(pose, pose_updated)
        torso_speed = self._torso_speed(pose, timestamp_s, pose_updated)

        hand_recent = (
            self._last_hand_seen_at is not None
            and timestamp_s - self._last_hand_seen_at <= self.config.hand_missing_grace_s
        )
        pose_recent = (
            self._last_pose_seen_at is not None
            and timestamp_s - self._last_pose_seen_at <= self.config.pose_missing_grace_s
        )

        if not stream_ok:
            desired, reason = JointState.FAULT, "camera_stream_fault"
        elif quality_issue:
            desired, reason = JointState.UNCERTAIN, quality_issue
        elif not hand_recent or not pose_recent:
            desired, reason = JointState.UNCERTAIN, "hand_or_upper_body_not_reliably_visible"
        elif machine.state == MachineState.UNCERTAIN or not machine.quality_ok:
            desired, reason = JointState.UNCERTAIN, f"machine_state_{machine.reason}"
        elif in_danger and machine.state == MachineState.PRESSING:
            desired, reason = JointState.DANGER, "hand_and_pressing_motion_conflict"
        elif len(hands) < 2:
            desired, reason = JointState.UNCERTAIN, "both_hands_not_reliably_visible"
        elif in_danger and danger_dwell >= self.config.danger_dwell_s:
            desired, reason = JointState.WARNING, "hand_dwell_in_danger_zone"
        elif (
            lean_change is not None
            and lean_change >= self.config.forward_lean_change_deg
        ) or torso_speed >= self.config.torso_approach_speed_threshold:
            desired, reason = JointState.WARNING, "upper_body_risk_trend"
        elif warning_clearance is not None and warning_clearance <= 0:
            desired, reason = JointState.WARNING, "hand_inside_warning_zone"
        elif predicted_entry is not None and predicted_entry <= self.config.prediction_horizon_s:
            desired, reason = JointState.ATTENTION, "hand_trajectory_deviation"
        elif elbow_change is not None and elbow_change >= 22.0:
            desired, reason = JointState.ATTENTION, "rapid_elbow_angle_change"
        else:
            desired, reason = JointState.NORMAL, "normal_joint_operation"

        transition = self._advance(desired)
        return JointAssessment(
            state=self.state,
            raw_state=desired,
            reason=reason,
            transition=transition,
            hand_count=len(hands),
            hand_confidence=hand_confidence,
            min_hand_clearance=min_clearance,
            hand_approach_speed=approach_speed,
            predicted_entry_s=predicted_entry,
            danger_dwell_s=danger_dwell,
            both_hands_withdrawn=both_withdrawn,
            left_elbow_angle=pose.left_elbow_angle if pose else None,
            right_elbow_angle=pose.right_elbow_angle if pose else None,
            elbow_change_deg=elbow_change,
            torso_lean_deg=lean,
            torso_lean_change_deg=lean_change,
            torso_approach_speed=torso_speed,
            pose_visibility=pose_visibility,
            pose_upper_body_visible=bool(pose and pose.upper_body_visible),
            machine_state=machine.state,
            machine_raw_state=machine.raw_state,
            machine_vertical_flow=machine.vertical_flow,
            machine_moving_fraction=machine.moving_fraction,
            machine_reference_motion=machine.reference_motion,
            machine_quality_ok=machine.quality_ok,
        )

    def fault(self, timestamp_s: float) -> JointAssessment:
        machine = MachineObservation(
            MachineState.UNCERTAIN,
            MachineState.UNCERTAIN,
            0.0,
            0.0,
            0.0,
            False,
            "stream_fault",
        )
        return self.update([], None, machine, timestamp_s, stream_ok=False)

    def _hand_speed(self, clearance: float | None, timestamp_s: float) -> float:
        raw = 0.0
        if clearance is not None and self._previous_clearance is not None and self._previous_hand_timestamp is not None:
            elapsed = timestamp_s - self._previous_hand_timestamp
            if 1e-4 < elapsed <= 1.0:
                raw = (self._previous_clearance - clearance) / elapsed
        alpha = self.config.speed_smoothing
        self._smoothed_speed = alpha * raw + (1.0 - alpha) * self._smoothed_speed
        self._previous_clearance = clearance
        self._previous_hand_timestamp = timestamp_s
        return max(0.0, self._smoothed_speed)

    def _torso_speed(
        self,
        pose: PoseObservation | None,
        timestamp_s: float,
        pose_updated: bool,
    ) -> float:
        if not pose_updated:
            return self._last_torso_speed
        speed = 0.0
        current = pose.torso_center if pose is not None else None
        if current is not None and self._previous_torso is not None and self._previous_torso_timestamp is not None:
            elapsed = timestamp_s - self._previous_torso_timestamp
            if 1e-4 < elapsed <= 1.0:
                speed = math.hypot(current[0] - self._previous_torso[0], current[1] - self._previous_torso[1]) / elapsed
        self._previous_torso = current
        self._previous_torso_timestamp = timestamp_s
        self._last_torso_speed = speed
        return self._last_torso_speed

    def _elbow_change(
        self,
        pose: PoseObservation | None,
        pose_updated: bool,
    ) -> float | None:
        if not pose_updated:
            return self._last_elbow_change
        current = (
            pose.left_elbow_angle if pose else None,
            pose.right_elbow_angle if pose else None,
        )
        changes = [
            abs(now - before)
            for now, before in zip(current, self._previous_elbows)
            if now is not None and before is not None
        ]
        self._previous_elbows = current
        self._last_elbow_change = max(changes) if changes else None
        return self._last_elbow_change

    def _advance(self, desired: JointState) -> tuple[JointState, JointState] | None:
        if desired == self.state:
            self._candidate = None
            self._candidate_frames = 0
            return None
        if desired != self._candidate:
            self._candidate, self._candidate_frames = desired, 1
        else:
            self._candidate_frames += 1
        if desired == JointState.FAULT:
            threshold = 1
        elif desired == JointState.UNCERTAIN:
            threshold = self.config.enter_uncertain_frames
        elif desired == JointState.DANGER:
            threshold = self.config.enter_danger_frames
        elif desired == JointState.WARNING:
            threshold = self.config.enter_warning_frames
        elif desired == JointState.ATTENTION:
            threshold = self.config.enter_attention_frames
        else:
            threshold = self.config.clear_frames
        if self._candidate_frames < threshold:
            return None
        previous = self.state
        self.state = desired
        self._candidate = None
        self._candidate_frames = 0
        return previous, desired


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 5)
