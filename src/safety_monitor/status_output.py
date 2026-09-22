"""Privacy-minimized live status snapshot for isolated display peripherals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .joint_engine import JointAssessment, JointState


_AGGREGATE_PRECEDENCE = (
    JointState.DANGER,
    JointState.FAULT,
    JointState.WARNING,
    JointState.UNCERTAIN,
    JointState.ATTENTION,
    JointState.NORMAL,
)


def aggregate_station_assessments(
    assessments: Mapping[str, JointAssessment],
) -> tuple[JointState, str]:
    """Fail closed across stations without allowing one fault to hide a known danger."""
    if not assessments:
        return JointState.FAULT, "no_station_assessment"
    for target in _AGGREGATE_PRECEDENCE:
        for station_id, assessment in assessments.items():
            if assessment.state == target:
                return target, f"{station_id}:{assessment.reason}"
    return JointState.FAULT, "station_state_invalid"


class LiveStatusWriter:
    """Atomically publish state metadata without images, identity, or control fields."""

    def __init__(self, path: str | Path, heartbeat_s: float = 0.5) -> None:
        self.path = Path(path)
        self.heartbeat_s = max(0.1, float(heartbeat_s))
        self._last_key: tuple[str, str] | None = None
        self._last_written_s: float | None = None
        self._sequence = 0

    def update(self, assessment: JointAssessment, timestamp_s: float) -> bool:
        key = (assessment.state.name, assessment.reason)
        heartbeat_due = (
            self._last_written_s is None
            or timestamp_s - self._last_written_s >= self.heartbeat_s
        )
        if key == self._last_key and not heartbeat_due:
            return False
        payload = {
            "schema_version": 1,
            "sequence": self._sequence,
            "timestamp_monotonic_s": round(float(timestamp_s), 3),
            "state": assessment.state.name,
            "reason": assessment.reason,
            "machine_state": assessment.machine_state.value,
            "hand_count": assessment.hand_count,
            "pose_upper_body_visible": assessment.pose_upper_body_visible,
            "advisory_only": True,
            "machine_control_enabled": False,
            "contains_image": False,
            "contains_identity": False,
        }
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


class MultiStationStatusWriter:
    """Publish one aggregate indicator state plus anonymous per-station evidence."""

    def __init__(self, path: str | Path, heartbeat_s: float = 0.5) -> None:
        self.path = Path(path)
        self.heartbeat_s = max(0.1, float(heartbeat_s))
        self._last_key: tuple[tuple[str, str, str], ...] | None = None
        self._last_written_s: float | None = None
        self._sequence = 0

    def update(
        self,
        assessments: Mapping[str, JointAssessment],
        timestamp_s: float,
    ) -> bool:
        ordered = tuple(
            sorted(
                (
                    str(station_id),
                    assessment.state.name,
                    assessment.reason,
                )
                for station_id, assessment in assessments.items()
            )
        )
        heartbeat_due = (
            self._last_written_s is None
            or timestamp_s - self._last_written_s >= self.heartbeat_s
        )
        if ordered == self._last_key and not heartbeat_due:
            return False
        aggregate_state, aggregate_reason = aggregate_station_assessments(assessments)
        stations = [
            {
                "station_id": station_id,
                "state": assessment.state.name,
                "reason": assessment.reason,
                "machine_state": assessment.machine_state.value,
                "hand_count": assessment.hand_count,
                "pose_upper_body_visible": assessment.pose_upper_body_visible,
            }
            for station_id, assessment in sorted(assessments.items())
        ]
        payload = {
            "schema_version": 1,
            "sequence": self._sequence,
            "timestamp_monotonic_s": round(float(timestamp_s), 3),
            "state": aggregate_state.name,
            "reason": aggregate_reason,
            "stations": stations,
            "advisory_only": True,
            "machine_control_enabled": False,
            "contains_image": False,
            "contains_identity": False,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self._last_key = ordered
        self._last_written_s = timestamp_s
        self._sequence += 1
        return True
