import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from safety_monitor.event_store import EventStore
from safety_monitor.risk_engine import RiskAssessment, RiskState


class EventStoreTests(unittest.TestCase):
    def test_transition_log_is_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EventStore(directory, "pilot_01", retention_days=30)
            store.append(
                RiskAssessment(
                    state=RiskState.WARNING,
                    raw_state=RiskState.WARNING,
                    reason="hand_inside_warning_zone",
                    hand_count=1,
                    min_clearance=0.08,
                    approach_speed=0.2,
                    predicted_entry_s=0.4,
                    transition=(RiskState.CLEAR, RiskState.WARNING),
                )
            )
            paths = list(Path(directory).glob("*.ndjson"))
            self.assertEqual(len(paths), 1)
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertFalse(payload["contains_image"])
            self.assertFalse(payload["contains_biometric_identifier"])
            self.assertNotIn("worker_id", payload)
            self.assertEqual(payload["station_id"], "pilot_01")

    def test_expired_daily_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            today = datetime.now(UTC).date()
            old_path = Path(directory) / f"{today - timedelta(days=31)}.ndjson"
            current_path = Path(directory) / f"{today}.ndjson"
            old_path.write_text("{}\n", encoding="utf-8")
            current_path.write_text("{}\n", encoding="utf-8")
            store = EventStore(directory, "pilot_01", retention_days=30)
            self.assertFalse(old_path.exists())
            self.assertTrue(current_path.exists())


if __name__ == "__main__":
    unittest.main()
