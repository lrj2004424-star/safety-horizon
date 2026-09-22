"""Anonymous, temporal whole-person fatigue-risk proxy.

This module deliberately does not diagnose fatigue.  It observes persistent
changes from each visible person's own posture and movement baseline and emits
an advisory risk level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from statistics import median

from .pose_tracker import PoseObservation


class FatigueState(Enum):
    OBSERVING = "observing"
    ACTIVE = "active"
    FATIGUE_TREND = "fatigue_trend"
    HIGH_RISK = "high_risk"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class PersonFatigueAssessment:
    track_id: int
    state: FatigueState
    score: int
    reason: str
    observed_seconds: float
    head_drop_change: float
    lean_change_deg: float
    shoulder_tilt_change_deg: float
    motion_rate: float
    posture_signal_count: int
    body_visibility: float
    pose: PoseObservation


@dataclass(frozen=True)
class FatigueAssessment:
    state: FatigueState
    score: int
    reason: str
    persons: tuple[PersonFatigueAssessment, ...]
    transition: tuple[FatigueState, FatigueState] | None
    advisory_only: bool = True


@dataclass
class _Track:
    track_id: int
    first_seen_s: float
    last_seen_s: float
    last_center: tuple[float, float]
    last_body_points: tuple[tuple[float, float], ...]
    baseline_head: list[float] = field(default_factory=list)
    baseline_lean: list[float] = field(default_factory=list)
    baseline_shoulder_tilt: list[float] = field(default_factory=list)
    baseline_ready: bool = False
    head_reference: float = 0.0
    lean_reference: float = 0.0
    shoulder_tilt_reference: float = 0.0
    motion_ema: float = 0.0
    combined_since_s: float | None = None
    trend_since_s: float | None = None
    last_assessment: PersonFatigueAssessment | None = None
    valid_updates: int = 0


class FatigueRiskEngine:
    """Track at most a few anonymous people and assess persistent change."""

    def __init__(
        self,
        *,
        baseline_seconds: float = 20.0,
        trend_seconds: float = 20.0,
        high_risk_seconds: float = 45.0,
        missing_reset_seconds: float = 30.0,
        visibility_grace_seconds: float = 3.5,
        max_people: int = 2,
        min_body_visibility: float = 0.30,
        max_assignment_distance: float = 0.32,
        min_torso_length: float = 0.025,
        min_body_coverage: float = 0.0,
        min_body_span: float = 0.0,
        min_track_confirmations: int = 1,
        require_lower_body_order: bool = False,
        warning_score_min: int = 45,
        danger_score_min: int = 70,
    ) -> None:
        if baseline_seconds <= 0 or trend_seconds <= 0 or high_risk_seconds < trend_seconds:
            raise ValueError("fatigue timing values are invalid")
        if not 1 <= int(warning_score_min) < int(danger_score_min) <= 100:
            raise ValueError("score thresholds must satisfy 1 <= warning < danger <= 100")
        self.baseline_seconds = float(baseline_seconds)
        self.trend_seconds = float(trend_seconds)
        self.high_risk_seconds = float(high_risk_seconds)
        self.missing_reset_seconds = float(missing_reset_seconds)
        self.visibility_grace_seconds = max(0.0, float(visibility_grace_seconds))
        self.max_people = max(1, int(max_people))
        self.min_body_visibility = float(min_body_visibility)
        self.max_assignment_distance = max(0.05, float(max_assignment_distance))
        self.min_torso_length = max(0.01, float(min_torso_length))
        self.min_body_coverage = min(1.0, max(0.0, float(min_body_coverage)))
        self.min_body_span = min(1.0, max(0.0, float(min_body_span)))
        self.min_track_confirmations = max(1, int(min_track_confirmations))
        self.require_lower_body_order = bool(require_lower_body_order)
        self.warning_score_min = int(warning_score_min)
        self.danger_score_min = int(danger_score_min)
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self._state = FatigueState.OBSERVING

    def update(
        self, observations: list[PoseObservation], timestamp_s: float
    ) -> FatigueAssessment:
        timestamp_s = float(timestamp_s)
        self._expire_tracks(timestamp_s)
        usable = [pose for pose in observations if self._features(pose) is not None]
        assignments = self._assign(usable[: self.max_people], timestamp_s)
        updated = [
            (track, self._update_track(track, pose, timestamp_s))
            for track, pose in assignments
        ]
        current_persons = [
            person
            for track, person in updated
            if track.valid_updates >= self.min_track_confirmations
        ]
        assigned_ids = {track.track_id for track, _ in assignments}
        cached_persons = [
            replace(
                track.last_assessment,
                reason="temporary_pose_gap",
                observed_seconds=max(0.0, timestamp_s - track.first_seen_s),
            )
            for track_id, track in self._tracks.items()
            if track_id not in assigned_ids
            and track.last_assessment is not None
            and track.valid_updates >= self.min_track_confirmations
            and timestamp_s - track.last_seen_s <= self.visibility_grace_seconds
        ]
        persons = tuple(sorted(current_persons + cached_persons, key=lambda item: item.track_id))
        if not persons:
            state = FatigueState.UNCERTAIN
            score = 0
            reason = "no_reliable_whole_person_pose"
        else:
            ranked = max(persons, key=lambda item: (_precedence(item.state), item.score))
            state, score = ranked.state, ranked.score
            reason = f"person_{ranked.track_id}:{ranked.reason}"
        transition = None if state == self._state else (self._state, state)
        self._state = state
        return FatigueAssessment(state, score, reason, persons, transition)

    def fault(self, reason: str = "window_capture_unavailable") -> FatigueAssessment:
        previous = self._state
        self._state = FatigueState.UNCERTAIN
        transition = None if previous == self._state else (previous, self._state)
        return FatigueAssessment(self._state, 0, reason, (), transition)

    def _assign(
        self, observations: list[PoseObservation], timestamp_s: float
    ) -> list[tuple[_Track, PoseObservation]]:
        available = set(self._tracks)
        result: list[tuple[_Track, PoseObservation]] = []
        ordered = sorted(observations, key=lambda pose: pose.torso_center[0] if pose.torso_center else 0.0)
        for pose in ordered:
            assert pose.torso_center is not None
            nearest = None
            nearest_distance = self.max_assignment_distance
            for track_id in available:
                track = self._tracks[track_id]
                distance = math.dist(track.last_center, pose.torso_center)
                if distance < nearest_distance:
                    nearest, nearest_distance = track_id, distance
            if nearest is None:
                if len(self._tracks) < self.max_people:
                    track = _Track(
                        track_id=self._next_track_id,
                        first_seen_s=timestamp_s,
                        last_seen_s=timestamp_s,
                        last_center=pose.torso_center,
                        last_body_points=self._body_points(pose),
                    )
                    self._tracks[track.track_id] = track
                    self._next_track_id += 1
                elif available:
                    nearest = min(
                        available,
                        key=lambda track_id: math.dist(
                            self._tracks[track_id].last_center, pose.torso_center
                        ),
                    )
                    track = self._tracks[nearest]
                    available.remove(nearest)
                else:
                    continue
            else:
                track = self._tracks[nearest]
                available.remove(nearest)
            result.append((track, pose))
        return result

    def _update_track(
        self, track: _Track, pose: PoseObservation, timestamp_s: float
    ) -> PersonFatigueAssessment:
        extracted = self._features(pose)
        assert extracted is not None and pose.torso_center is not None
        head_drop, lean, shoulder_tilt, body_visibility = extracted
        body_points = self._body_points(pose)
        gap = timestamp_s - track.last_seen_s
        if gap > self.visibility_grace_seconds:
            track.valid_updates = 0
        track.valid_updates += 1
        dt = max(1e-3, gap)
        motion = self._body_motion(track.last_body_points, body_points) / dt
        track.motion_ema = motion if track.last_seen_s == track.first_seen_s else 0.82 * track.motion_ema + 0.18 * motion
        track.last_seen_s = timestamp_s
        track.last_center = pose.torso_center
        track.last_body_points = body_points
        observed = max(0.0, timestamp_s - track.first_seen_s)

        if not track.baseline_ready:
            track.baseline_head.append(head_drop)
            track.baseline_lean.append(lean)
            track.baseline_shoulder_tilt.append(shoulder_tilt)
            if observed >= self.baseline_seconds and len(track.baseline_head) >= 8:
                track.head_reference = float(median(track.baseline_head))
                track.lean_reference = float(median(track.baseline_lean))
                track.shoulder_tilt_reference = float(median(track.baseline_shoulder_tilt))
                track.baseline_ready = True
            result = PersonFatigueAssessment(
                track.track_id,
                FatigueState.OBSERVING,
                0,
                "building_personal_work_baseline",
                observed,
                0.0,
                0.0,
                0.0,
                track.motion_ema,
                0,
                body_visibility,
                pose,
            )
            track.last_assessment = result
            return result

        head_change = max(0.0, head_drop - track.head_reference)
        lean_change = abs(lean - track.lean_reference)
        tilt_change = max(0.0, shoulder_tilt - track.shoulder_tilt_reference)
        head_strength = _scaled(head_change, 0.07, 0.24)
        lean_strength = _scaled(lean_change, 7.0, 22.0)
        tilt_strength = _scaled(tilt_change, 5.0, 18.0)
        low_motion = track.motion_ema < 0.004
        low_motion_strength = 1.0 if low_motion else 0.0
        strong = (
            int(head_change >= 0.10)
            + int(lean_change >= 11.0)
            + int(tilt_change >= 9.0)
        )
        combined = strong >= 2 or (strong >= 1 and low_motion)
        if combined:
            track.combined_since_s = track.combined_since_s or timestamp_s
        else:
            track.combined_since_s = None
            track.trend_since_s = None
        dwell = 0.0 if track.combined_since_s is None else timestamp_s - track.combined_since_s
        raw_score = round(
            min(
                100.0,
                43.0 * head_strength
                + 32.0 * lean_strength
                + 17.0 * tilt_strength
                + 8.0 * low_motion_strength,
            )
        )
        if strong >= 2 and dwell >= self.high_risk_seconds and raw_score >= 60:
            state = FatigueState.HIGH_RISK
            reason = "multiple_posture_changes_persisted"
            score = max(self.danger_score_min, raw_score)
        elif combined and dwell >= self.trend_seconds:
            state = FatigueState.FATIGUE_TREND
            reason = "sustained_posture_and_activity_change"
            track.trend_since_s = track.trend_since_s or timestamp_s
            # Keep the externally documented score bands mutually exclusive:
            # ACTIVE 0-44, FATIGUE_TREND 45-69, HIGH_RISK 70-100.
            score = max(
                self.warning_score_min,
                min(self.danger_score_min - 1, raw_score),
            )
        else:
            state = FatigueState.ACTIVE
            reason = "work_pattern_within_personal_baseline"
            if combined:
                persistence = min(1.0, dwell / self.trend_seconds)
                score = min(
                    self.warning_score_min - 1,
                    round(raw_score * (0.2 + 0.8 * persistence)),
                )
            else:
                score = min(min(30, self.warning_score_min - 1), raw_score)
        result = PersonFatigueAssessment(
            track.track_id,
            state,
            score,
            reason,
            observed,
            head_change,
            lean_change,
            tilt_change,
            track.motion_ema,
            strong,
            body_visibility,
            pose,
        )
        track.last_assessment = result
        return result

    def _features(self, pose: PoseObservation) -> tuple[float, float, float, float] | None:
        if pose.shoulder_center is None or pose.hip_center is None or pose.torso_lean_deg is None:
            return None
        landmarks = pose.landmarks
        if len(landmarks) < 29:
            return None
        torso_length = math.dist(pose.shoulder_center, pose.hip_center)
        if torso_length < self.min_torso_length:
            return None
        if pose.hip_center[1] <= pose.shoulder_center[1] + 0.02:
            return None
        if abs(pose.torso_lean_deg) > 60.0:
            return None
        visible_head = [
            point
            for point in landmarks[:11]
            if point.visibility >= min(0.25, self.min_body_visibility)
        ]
        if not visible_head:
            return None
        head_y = sum(point.y for point in visible_head) / len(visible_head)
        if head_y >= pose.hip_center[1]:
            return None
        head_drop = (head_y - pose.shoulder_center[1]) / torso_length
        left, right = landmarks[11], landmarks[12]
        shoulder_tilt = math.degrees(
            math.atan2(abs(right.y - left.y), max(1e-6, abs(right.x - left.x)))
        )
        if shoulder_tilt > 40.0:
            return None
        body_indices = (0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28)
        visible_points = [
            landmarks[index]
            for index in body_indices
            if landmarks[index].visibility >= self.min_body_visibility
        ]
        coverage = len(visible_points) / len(body_indices)
        if coverage < self.min_body_coverage:
            return None
        span = (
            max(point.y for point in visible_points)
            - min(point.y for point in visible_points)
            if visible_points else 0.0
        )
        if span < self.min_body_span:
            return None
        if self.require_lower_body_order:
            knees = [
                landmarks[index].y
                for index in (25, 26)
                if landmarks[index].visibility >= self.min_body_visibility
            ]
            ankles = [
                landmarks[index].y
                for index in (27, 28)
                if landmarks[index].visibility >= self.min_body_visibility
            ]
            if not knees or not ankles:
                return None
            knee_y = sum(knees) / len(knees)
            ankle_y = sum(ankles) / len(ankles)
            if knee_y <= pose.hip_center[1] - 0.02 or ankle_y <= knee_y - 0.02:
                return None
        return head_drop, pose.torso_lean_deg, shoulder_tilt, coverage

    def _body_points(self, pose: PoseObservation) -> tuple[tuple[float, float], ...]:
        indices = (0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28)
        return tuple(
            (pose.landmarks[index].x, pose.landmarks[index].y)
            for index in indices
            if pose.landmarks[index].visibility >= self.min_body_visibility
        )

    @staticmethod
    def _body_motion(
        previous: tuple[tuple[float, float], ...], current: tuple[tuple[float, float], ...]
    ) -> float:
        if not previous or not current:
            return 0.0
        count = min(len(previous), len(current))
        return sum(math.dist(previous[index], current[index]) for index in range(count)) / count

    def _expire_tracks(self, timestamp_s: float) -> None:
        stale = [
            track_id
            for track_id, track in self._tracks.items()
            if timestamp_s - track.last_seen_s > self.missing_reset_seconds
        ]
        for track_id in stale:
            del self._tracks[track_id]


def _scaled(value: float, start: float, full: float) -> float:
    return min(1.0, max(0.0, (value - start) / max(1e-6, full - start)))


def _precedence(state: FatigueState) -> int:
    return {
        FatigueState.OBSERVING: 1,
        FatigueState.ACTIVE: 2,
        FatigueState.UNCERTAIN: 3,
        FatigueState.FATIGUE_TREND: 4,
        FatigueState.HIGH_RISK: 5,
    }[state]
