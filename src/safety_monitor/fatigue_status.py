"""Atomic, image-free status output for the fatigue-risk monitor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from .fatigue_engine import FatigueAssessment, FatigueState


# Stable numeric contract for status consumers such as Arduino.  OBSERVING and
# UNCERTAIN deliberately have their own state codes because both carry score 0
# but neither is equivalent to a confirmed ACTIVE worker.
FATIGUE_STATE_CODES = {
    FatigueState.ACTIVE: 0,
    FatigueState.OBSERVING: 1,
    FatigueState.UNCERTAIN: 2,
    FatigueState.FATIGUE_TREND: 3,
    FatigueState.HIGH_RISK: 4,
}

FATIGUE_BUZZER_LEVELS = {
    FatigueState.ACTIVE: 0,
    FatigueState.OBSERVING: 0,
    # User-selected audible policy: uncertainty remains visible in state_code
    # and JSON but does not sound the buzzer. Serial/system FAULT stays audible.
    FatigueState.UNCERTAIN: 0,
    FatigueState.FATIGUE_TREND: 2,
    FatigueState.HIGH_RISK: 3,
}


def fatigue_state_code(state: FatigueState) -> int:
    return FATIGUE_STATE_CODES.get(state, 2)


def fatigue_buzzer_level(state: FatigueState) -> int:
    return FATIGUE_BUZZER_LEVELS.get(state, 1)


_FATIGUE_PRECEDENCE = (
    FatigueState.HIGH_RISK,
    FatigueState.FATIGUE_TREND,
    FatigueState.UNCERTAIN,
    FatigueState.OBSERVING,
    FatigueState.ACTIVE,
)


def aggregate_fatigue_assessments(
    assessments: Mapping[str, FatigueAssessment],
) -> tuple[FatigueState, int, str]:
    """Keep a known high risk visible while otherwise failing closed."""
    if not assessments:
        return FatigueState.UNCERTAIN, 0, "no_station_assessment"
    for target in _FATIGUE_PRECEDENCE:
        candidates = [
            (station_id, assessment)
            for station_id, assessment in assessments.items()
            if assessment.state == target
        ]
        if candidates:
            station_id, assessment = max(
                candidates, key=lambda item: item[1].score
            )
            return target, assessment.score, f"{station_id}:{assessment.reason}"
    return FatigueState.UNCERTAIN, 0, "station_state_invalid"


class FatigueStatusWriter:
    def __init__(self, path: str | Path, heartbeat_s: float = 0.5) -> None:
        self.path = Path(path)
        self.heartbeat_s = max(0.1, float(heartbeat_s))
        self._last_written_s: float | None = None
        self._last_key: tuple[str, str, tuple[tuple[int, str, int], ...]] | None = None
        self._sequence = 0

    def update(self, assessment: FatigueAssessment, timestamp_s: float) -> bool:
        people_key = tuple((person.track_id, person.state.name, person.score) for person in assessment.persons)
        key = (assessment.state.name, assessment.reason, people_key)
        due = self._last_written_s is None or timestamp_s - self._last_written_s >= self.heartbeat_s
        if key == self._last_key and not due:
            return False
        payload = {
            "schema_version": 2,
            "sequence": self._sequence,
            "timestamp_monotonic_s": round(float(timestamp_s), 3),
            "mode": "whole_person_fatigue_risk",
            "state": assessment.state.name,
            "score": assessment.score,
            "risk_score": assessment.score,
            "state_code": fatigue_state_code(assessment.state),
            "buzzer_level": fatigue_buzzer_level(assessment.state),
            "reason": assessment.reason,
            "people": [
                {
                    "track_id": person.track_id,
                    "state": person.state.name,
                    "score": person.score,
                    "risk_score": person.score,
                    "state_code": fatigue_state_code(person.state),
                    "buzzer_level": fatigue_buzzer_level(person.state),
                    "observed_seconds": round(person.observed_seconds, 1),
                    "head_drop_change": round(person.head_drop_change, 3),
                    "lean_change_deg": round(person.lean_change_deg, 1),
                    "shoulder_tilt_change_deg": round(person.shoulder_tilt_change_deg, 1),
                    "motion_rate": round(person.motion_rate, 4),
                    "body_visibility": round(person.body_visibility, 2),
                }
                for person in assessment.persons
            ],
            "advisory_only": True,
            "medical_diagnosis": False,
            "contains_image": False,
            "contains_identity": False,
            "machine_control_enabled": False,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        self._last_key = key
        self._last_written_s = timestamp_s
        self._sequence += 1
        return True


class MultiStationFatigueStatusWriter:
    """Publish aggregate plus anonymous per-station fatigue metadata."""

    def __init__(
        self,
        path: str | Path,
        heartbeat_s: float = 0.5,
        *,
        context_provider: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.heartbeat_s = max(0.1, float(heartbeat_s))
        self._last_written_s: float | None = None
        self._last_key: tuple[tuple[str, str, int, str], ...] | None = None
        self._sequence = 0
        self._context_provider = context_provider

    def update(
        self,
        assessments: Mapping[str, FatigueAssessment],
        timestamp_s: float,
    ) -> bool:
        key = tuple(
            sorted(
                (
                    station_id,
                    assessment.state.name,
                    assessment.score,
                    assessment.reason,
                )
                for station_id, assessment in assessments.items()
            )
        )
        due = (
            self._last_written_s is None
            or timestamp_s - self._last_written_s >= self.heartbeat_s
        )
        if key == self._last_key and not due:
            return False
        state, score, reason = aggregate_fatigue_assessments(assessments)
        payload = {
            "schema_version": 3,
            "sequence": self._sequence,
            "timestamp_monotonic_s": round(float(timestamp_s), 3),
            "mode": "ezviz_multistation_whole_person_fatigue",
            "state": state.name,
            "score": score,
            "risk_score": score,
            "state_code": fatigue_state_code(state),
            "buzzer_level": fatigue_buzzer_level(state),
            "reason": reason,
            "stations": [
                {
                    "station_id": station_id,
                    "state": assessment.state.name,
                    "score": assessment.score,
                    "risk_score": assessment.score,
                    "state_code": fatigue_state_code(assessment.state),
                    "buzzer_level": fatigue_buzzer_level(assessment.state),
                    "reason": assessment.reason,
                    "people": [
                        {
                            "track_id": person.track_id,
                            "state": person.state.name,
                            "score": person.score,
                            "risk_score": person.score,
                            "state_code": fatigue_state_code(person.state),
                            "buzzer_level": fatigue_buzzer_level(person.state),
                            "observed_seconds": round(person.observed_seconds, 1),
                            "head_drop_change": round(person.head_drop_change, 3),
                            "lean_change_deg": round(person.lean_change_deg, 1),
                            "shoulder_tilt_change_deg": round(
                                person.shoulder_tilt_change_deg, 1
                            ),
                            "motion_rate": round(person.motion_rate, 4),
                            "body_visibility": round(person.body_visibility, 2),
                        }
                        for person in assessment.persons
                    ],
                }
                for station_id, assessment in sorted(assessments.items())
            ],
            "advisory_only": True,
            "medical_diagnosis": False,
            "contains_image": False,
            "contains_identity": False,
            "machine_control_enabled": False,
        }
        if self._context_provider is not None:
            try:
                context = self._context_provider()
            except Exception:
                context = {}
            if isinstance(context, Mapping):
                # Only these non-image routing fields may enter status JSON.
                for name in (
                    "input_mode",
                    "simulation",
                    "physical_actuator_allowed",
                    "test_video_name",
                    "threshold_version",
                    "warning_min",
                    "danger_min",
                ):
                    if name in context:
                        payload[name] = context[name]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self._last_key = key
        self._last_written_s = timestamp_s
        self._sequence += 1
        return True
