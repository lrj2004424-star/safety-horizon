"""Temporal hand-to-hazard risk estimation with debounce and hysteresis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

from .config import RiskConfig
from .geometry import Point, signed_distance_to_polygon


class RiskState(IntEnum):
    CLEAR = 0
    WARNING = 1
    DANGER = 2


@dataclass(frozen=True)
class RiskAssessment:
    state: RiskState
    raw_state: RiskState
    reason: str
    hand_count: int
    min_clearance: float | None
    approach_speed: float
    predicted_entry_s: float | None
    transition: tuple[RiskState, RiskState] | None


class RiskEngine:
    """Classify each frame while suppressing one-frame alert flicker.

    Distances and speeds are normalized by image size. This engine is an
    advisory prototype; it is not a safety-rated machine control component.
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = RiskState.CLEAR
        self._candidate: RiskState | None = None
        self._candidate_frames = 0
        self._previous_clearance: float | None = None
        self._previous_timestamp: float | None = None
        self._smoothed_speed = 0.0

    def reset(self) -> None:
        self.__init__(self.config)

    def update(
        self, hands: list[list[Point]], timestamp_s: float
    ) -> RiskAssessment:
        points = [
            hand[index]
            for hand in hands
            for index in self.config.relevant_landmarks
            if index < len(hand)
        ]

        if not points:
            min_clearance = None
            warning_clearance = None
            approach_speed = 0.0
            predicted_entry_s = None
            desired = RiskState.CLEAR
            reason = "no_hand_detected"
            self._previous_clearance = None
            self._previous_timestamp = timestamp_s
            self._smoothed_speed = 0.0
        else:
            min_clearance = min(
                signed_distance_to_polygon(point, self.config.danger_zone)
                for point in points
            )
            warning_clearance = min(
                signed_distance_to_polygon(point, self.config.warning_zone)
                for point in points
            )
            approach_speed = self._approach_speed(min_clearance, timestamp_s)
            predicted_entry_s = (
                max(0.0, min_clearance / approach_speed)
                if min_clearance > 0
                and approach_speed >= self.config.approach_speed_threshold
                else None
            )
            predicted_clearance = (
                min_clearance
                - approach_speed * self.config.prediction_horizon_s
            )

            if min_clearance <= 0:
                desired = RiskState.DANGER
                reason = "hand_inside_danger_zone"
            elif warning_clearance <= 0:
                desired = RiskState.WARNING
                reason = "hand_inside_warning_zone"
            elif (
                approach_speed >= self.config.approach_speed_threshold
                and predicted_clearance <= 0
            ):
                desired = RiskState.WARNING
                reason = "trajectory_toward_danger_zone"
            else:
                desired = RiskState.CLEAR
                reason = "hand_clear_of_monitored_zones"

        transition = self._advance_state(desired)
        return RiskAssessment(
            state=self.state,
            raw_state=desired,
            reason=reason,
            hand_count=len(hands),
            min_clearance=min_clearance,
            approach_speed=approach_speed,
            predicted_entry_s=predicted_entry_s,
            transition=transition,
        )

    def _approach_speed(self, clearance: float, timestamp_s: float) -> float:
        raw_speed = 0.0
        if self._previous_clearance is not None and self._previous_timestamp is not None:
            elapsed = timestamp_s - self._previous_timestamp
            if 1e-4 < elapsed <= 1.0:
                raw_speed = (self._previous_clearance - clearance) / elapsed
        alpha = self.config.speed_smoothing
        self._smoothed_speed = alpha * raw_speed + (1.0 - alpha) * self._smoothed_speed
        if not math.isfinite(self._smoothed_speed):
            self._smoothed_speed = 0.0
        self._previous_clearance = clearance
        self._previous_timestamp = timestamp_s
        return max(0.0, self._smoothed_speed)

    def _advance_state(
        self, desired: RiskState
    ) -> tuple[RiskState, RiskState] | None:
        if desired == self.state:
            self._candidate = None
            self._candidate_frames = 0
            return None

        if desired != self._candidate:
            self._candidate = desired
            self._candidate_frames = 1
        else:
            self._candidate_frames += 1

        if desired < self.state:
            threshold = self.config.clear_frames
        elif desired == RiskState.DANGER:
            threshold = self.config.enter_danger_frames
        else:
            threshold = self.config.enter_warning_frames

        if self._candidate_frames < threshold:
            return None

        previous = self.state
        self.state = desired
        self._candidate = None
        self._candidate_frames = 0
        return previous, self.state
