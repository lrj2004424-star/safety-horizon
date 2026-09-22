import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from safety_monitor.review_contract import actuator_is_authorized
from safety_monitor.review_controller import ReviewController
from safety_monitor.review_store import ReviewStore
from safety_officer_review import SafetyOfficerService


def snapshot(state="HIGH_RISK", score=82, *, input_mode="LIVE"):
    return {
        "schema_version": 3,
        "session_id": f"test-{input_mode.lower()}",
        "sequence": 1,
        "timestamp_monotonic_s": 1.0,
        "state": state,
        "risk_score": score,
        "reason": "test_evidence",
        "stations": [
            {
                "station_id": "upper_station",
                "state": state,
                "risk_score": score,
                "reason": "test_evidence",
                "people": [],
            }
        ],
        "input_mode": input_mode,
        "machine_control_enabled": False,
    }


class ReviewGateTests(unittest.TestCase):
    def test_only_submitted_human_level_three_or_more_authorizes_sound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)
            controller = ReviewController(root, now=lambda: now)
            controller.start()
            event = controller.ingest_payload(
                snapshot(),
                clip_by_station={
                    "upper_station": {
                        "status": "READY",
                        "path": str(root / "clip.mp4"),
                        "face_blurred": True,
                    }
                },
            )[0]
            self.assertFalse(actuator_is_authorized(controller.store.read_actuator(), now=now))
            result = controller.submit_review(
                event["event_id"],
                review_level=3,
                primary_action="VIOLATION",
                hand_zone="DANGER",
                material_occluded=False,
            )
            self.assertTrue(actuator_is_authorized(result["actuator"], now=now))
            self.assertEqual(result["actuator"]["pattern_id"], "TWO_SHORT_ONE_LONG")
            self.assertEqual(result["actuator"]["repeat_count"], 1)

    def test_level_one_or_two_is_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)
            controller = ReviewController(root, now=lambda: now)
            controller.start()
            event = controller.ingest_payload(
                snapshot("FATIGUE_TREND", 55),
                clip_by_station={"upper_station": {"status": "UNAVAILABLE"}},
            )[0]
            result = controller.submit_review(
                event["event_id"],
                review_level=2,
                primary_action="NORMAL_PICK",
                material_occluded=False,
            )
            self.assertEqual(result["actuator"]["action"], "SILENT")
            self.assertFalse(actuator_is_authorized(result["actuator"], now=now))

    def test_simulation_records_do_not_enter_field_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root / "annotations", actuator_path=root / "actuator.json")
            item = store.ingest_snapshot(snapshot(input_mode="TEST"), station_id="upper_station")
            self.assertEqual(item["sample_origin"], "SIMULATION")
            store.update_clip(item["event_id"], {"status": "UNAVAILABLE"})
            store.submit_review(
                item["event_id"],
                review_level=5,
                primary_action="VIOLATION",
                material_occluded=False,
            )
            stats = store.statistics(station_id="upper_station")
            self.assertEqual(stats["reviewed_count"], 0)
            self.assertEqual(stats["simulation_reviewed_count"], 1)


class SafetyOfficerCommandTests(unittest.TestCase):
    def _write_command(self, service, payload):
        ReviewStore._write_json_atomic(service.command_path, payload)
        service._process_command()

    def test_threshold_application_requires_explicit_ready_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SafetyOfficerService(root)
            service.start()
            try:
                proposal = {
                    "schema_version": 1,
                    "status": "PROPOSED",
                    "generated_at_utc": "2026-09-13T01:00:00.000Z",
                    "auto_apply": False,
                    "requires_explicit_approval": True,
                    "suggested": {"warning_min": 42, "danger_min": 68},
                }
                ReviewStore._write_json_atomic(service.proposal_path, proposal)
                self._write_command(
                    service,
                    {"command_id": "cmd-no", "action": "APPLY_THRESHOLDS"},
                )
                self.assertFalse(service.last_result["ok"])
                self.assertFalse(service.active_thresholds_path.exists())
                self._write_command(
                    service,
                    {
                        "command_id": "cmd-yes",
                        "action": "APPLY_THRESHOLDS",
                        "explicit_approval": True,
                    },
                )
                active = json.loads(service.active_thresholds_path.read_text(encoding="utf-8"))
                self.assertTrue(service.last_result["ok"])
                self.assertEqual(active["thresholds"], {"warning_min": 42, "danger_min": 68})
                self.assertTrue(active["explicit_approval"])
            finally:
                service.stop()

    def test_test_mode_human_review_never_authorizes_sound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SafetyOfficerService(root)
            service.start()
            try:
                ReviewStore._write_json_atomic(
                    service.input_mode_path,
                    {
                        "schema_version": 1,
                        "mode": "TEST",
                        "test_video": str(root / "test_videos" / "scenario.mp4"),
                        "physical_actuator_allowed": False,
                    },
                )
                event = service.controller.ingest_payload(snapshot(input_mode="TEST"))[0]
                service.controller.update_clip(event["event_id"], {"status": "UNAVAILABLE"})
                self._write_command(
                    service,
                    {
                        "command_id": "cmd-submit-test",
                        "action": "SUBMIT_REVIEW",
                        "event_id": event["event_id"],
                        "review_level": 5,
                        "primary_action": "VIOLATION",
                        "hand_zone": "DANGER",
                        "material_occluded": False,
                    },
                )
                actuator = service.controller.store.read_actuator()
                self.assertEqual(actuator["action"], "SILENT")
                self.assertIn("test_mode", actuator["reason"])
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()

