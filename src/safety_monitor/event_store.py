"""Anonymous, metadata-only event log with local retention enforcement."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .risk_engine import RiskAssessment
from .joint_engine import JointAssessment


class EventStore:
    def __init__(self, root: str | Path, station_id: str, retention_days: int = 30):
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.station_id = station_id
        self.retention_days = retention_days
        self.purge_expired()

    def append(self, assessment: RiskAssessment) -> None:
        if assessment.transition is None:
            return
        previous, current = assessment.transition
        now = datetime.now(UTC)
        payload = {
            "timestamp_utc": now.isoformat(),
            "station_id": self.station_id,
            "event_type": "risk_state_transition",
            "from_state": previous.name,
            "to_state": current.name,
            "reason": assessment.reason,
            "hand_count": assessment.hand_count,
            "min_clearance_normalized": _rounded(assessment.min_clearance),
            "approach_speed_normalized_per_s": _rounded(assessment.approach_speed),
            "predicted_entry_s": _rounded(assessment.predicted_entry_s),
            "contains_image": False,
            "contains_biometric_identifier": False,
        }
        path = self.root / f"{now.date().isoformat()}.ndjson"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_joint(self, assessment: JointAssessment) -> None:
        """Write a joint-state transition without pixels or personal identifiers."""
        if assessment.transition is None:
            return
        previous, current = assessment.transition
        now = datetime.now(UTC)
        payload = {
            "timestamp_utc": now.isoformat(),
            "station_id": self.station_id,
            "event_type": "joint_risk_state_transition",
            "from_state": previous.name,
            "to_state": current.name,
            **assessment.feature_dict(),
            "contains_image": False,
            "contains_biometric_identifier": False,
        }
        path = self.root / f"{now.date().isoformat()}.ndjson"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def purge_expired(self, today: date | None = None) -> int:
        cutoff = (today or datetime.now(UTC).date()) - timedelta(
            days=self.retention_days
        )
        removed = 0
        for path in self.root.glob("*.ndjson"):
            try:
                event_day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if event_day < cutoff:
                path.unlink()
                removed += 1
        return removed


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
