import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from indicator_bridge import (
    command_from_snapshot,
    encode_command,
    encode_fatigue_command,
    state_from_snapshot,
)
from safety_monitor.config import RiskConfig
from safety_monitor.joint_engine import JointRiskEngine, JointState
from safety_monitor.machine_motion import MachineObservation, MachineState
from safety_monitor.status_output import (
    LiveStatusWriter,
    MultiStationStatusWriter,
    aggregate_station_assessments,
)


def config() -> RiskConfig:
    return RiskConfig(
        danger_zone=((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)),
        warning_zone=((0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)),
        relevant_landmarks=(0,),
        enter_attention_frames=1,
        enter_warning_frames=1,
        enter_danger_frames=1,
        enter_uncertain_frames=1,
        clear_frames=1,
        prediction_horizon_s=0.5,
        approach_speed_threshold=0.2,
        speed_smoothing=1.0,
        danger_dwell_s=0.2,
        hand_missing_grace_s=0.1,
        pose_missing_grace_s=0.1,
        min_hand_confidence=0.5,
        forward_lean_change_deg=12.0,
        torso_approach_speed_threshold=0.5,
    )


class StatusOutputTests(unittest.TestCase):
    def test_status_snapshot_contains_no_image_identity_or_control(self) -> None:
        assessment = JointRiskEngine(config()).fault(0.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            writer = LiveStatusWriter(path, heartbeat_s=0.5)
            self.assertTrue(writer.update(assessment, 0.0))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "FAULT")
            self.assertFalse(payload["machine_control_enabled"])
            self.assertFalse(payload["contains_image"])
            self.assertFalse(payload["contains_identity"])
            self.assertNotIn("command", payload)

    def test_writer_emits_changes_and_periodic_heartbeat(self) -> None:
        assessment = JointRiskEngine(config()).fault(0.0)
        with tempfile.TemporaryDirectory() as directory:
            writer = LiveStatusWriter(Path(directory) / "status.json", heartbeat_s=0.5)
            self.assertTrue(writer.update(assessment, 0.0))
            self.assertFalse(writer.update(assessment, 0.2))
            self.assertTrue(writer.update(assessment, 0.5))

    def test_indicator_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(
                json.dumps({"state": "NORMAL", "machine_control_enabled": True}),
                encoding="utf-8",
            )
            state, reason = state_from_snapshot(path, max_age_s=1.0)
        self.assertEqual(state, "FAULT")
        self.assertEqual(reason, "unsafe_status_contract")
        self.assertEqual(encode_command("not-a-state"), b"STATE FAULT\n")

    def test_valid_indicator_snapshot_is_encoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(
                json.dumps({"state": "WARNING", "machine_control_enabled": False}),
                encoding="utf-8",
            )
            state, _ = state_from_snapshot(path, max_age_s=1.0)
            self.assertEqual(encode_command(state), b"STATE WARNING\n")

    def test_fatigue_snapshot_is_encoded_for_buzzer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "state": "FATIGUE_TREND",
                        "risk_score": 58,
                        "machine_control_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            command, _ = command_from_snapshot(path, max_age_s=1.0)
            self.assertEqual(command, b"FATIGUE 3 58 2\n")
            self.assertEqual(
                encode_fatigue_command("HIGH_RISK", 94),
                b"FATIGUE 4 94 3\n",
            )

    def test_uncertain_keeps_distinct_state_code_when_buzzer_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "state": "UNCERTAIN",
                        "score": 0,
                        "machine_control_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            command, _ = command_from_snapshot(path, max_age_s=1.0)
            self.assertEqual(command, b"FATIGUE 2 0 0\n")

    def test_multistation_aggregate_keeps_known_danger_visible(self) -> None:
        base = JointRiskEngine(config()).fault(0.0)
        danger = replace(base, state=JointState.DANGER, reason="known_conflict")
        fault = replace(base, state=JointState.FAULT, reason="camera_stream_fault")
        state, reason = aggregate_station_assessments(
            {"upper": fault, "lower": danger}
        )
        self.assertEqual(state.name, "DANGER")
        self.assertEqual(reason, "lower:known_conflict")

    def test_multistation_snapshot_contains_per_station_metadata_only(self) -> None:
        base = JointRiskEngine(config()).fault(0.0)
        warning = replace(base, state=JointState.WARNING, reason="risk_trend")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "multi-status.json"
            writer = MultiStationStatusWriter(path)
            self.assertTrue(
                writer.update({"upper": base, "lower": warning}, 0.0)
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "FAULT")
            self.assertEqual(len(payload["stations"]), 2)
            self.assertFalse(payload["contains_image"])
            self.assertFalse(payload["contains_identity"])
            self.assertFalse(payload["machine_control_enabled"])


if __name__ == "__main__":
    unittest.main()
