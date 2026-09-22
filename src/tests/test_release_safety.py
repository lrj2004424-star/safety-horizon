"""Release regressions: no physical outputs, all evidence in temporary dirs."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from computer_buzzer_simulator import ReviewAudioPlayer
import indicator_bridge
from factory_preflight import inspect_project
from safety_monitor.review_contract import ContractError, build_actuator_command
from safety_monitor.review_store import ReviewStore
from safety_officer_review import SafetyOfficerService
from test_review_workflow import snapshot


class ReleaseSafetyTests(unittest.TestCase):
    def test_no_transient_alert_for_test_or_invalid_mode(self):
        for origin, mode in [("TEST", "LIVE"), ("LIVE", "TEST"), ("LIVE", "INVALID")]:
            with self.subTest(origin=origin, mode=mode), tempfile.TemporaryDirectory() as tmp:
                service = SafetyOfficerService(tmp)
                service.start()
                try:
                    item = service.controller.ingest_payload(snapshot(input_mode=origin))[0]
                    service.controller.update_clip(item["event_id"], {"status": "UNAVAILABLE"})
                    ReviewStore._write_json_atomic(service.input_mode_path, {
                        "mode": mode, "physical_actuator_allowed": mode == "LIVE",
                    })
                    ReviewStore._write_json_atomic(service.command_path, {
                        "command_id": "submit-one", "action": "SUBMIT_REVIEW",
                        "event_id": item["event_id"], "review_level": 5,
                        "primary_action": "VIOLATION", "material_occluded": False,
                    })
                    writes = []
                    original = ReviewStore._write_json_atomic
                    def observe(path, payload):
                        if path == service.controller.store.actuator_path:
                            writes.append(dict(payload))
                        return original(path, payload)
                    with patch.object(ReviewStore, "_write_json_atomic", side_effect=observe):
                        service._process_command()
                    self.assertTrue(service.last_result["ok"], service.last_result)
                    self.assertTrue(writes)
                    self.assertTrue(all(v["action"] == "SILENT" for v in writes), writes)
                finally:
                    service.stop()

    def test_duplicate_does_not_renew_or_overwrite_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewStore(Path(tmp) / "annotations", actuator_path=Path(tmp) / "actuator.json")
            item = store.ingest_snapshot(snapshot(), station_id="upper_station")
            store.update_clip(item["event_id"], {"status": "UNAVAILABLE"})
            kwargs = dict(review_level=4, primary_action="VIOLATION", material_occluded=False)
            record, first = store.submit_review(item["event_id"], **kwargs)
            _, repeated = store.submit_review(item["event_id"], **kwargs)
            self.assertEqual(first, repeated)
            silent = store.publish_silent("acknowledged")
            _, duplicate = store.submit_review(item["event_id"], **kwargs)
            self.assertEqual(duplicate["action"], "SILENT")
            self.assertEqual(store.read_actuator(), silent)
            with self.assertRaises(ContractError):
                build_actuator_command(record, active_seconds=float("nan"))

    def test_simulation_contract_itself_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewStore(Path(tmp) / "annotations", actuator_path=Path(tmp) / "actuator.json")
            item = store.ingest_snapshot(snapshot(input_mode="TEST"), station_id="upper_station")
            store.update_clip(item["event_id"], {"status": "UNAVAILABLE"})
            record, actuator = store.submit_review(item["event_id"], review_level=5,
                primary_action="VIOLATION", material_occluded=False)
            self.assertEqual(record["derived"]["buzzer_action"], "SILENT")
            self.assertEqual(actuator["action"], "SILENT")

    def test_manual_sample_starts_evidence_and_rejects_stale_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SafetyOfficerService(tmp)
            service.start()
            try:
                ReviewStore._write_json_atomic(service.controller.status_path, snapshot())
                with patch.object(service.clips, "start_event", return_value={"status": "RECORDING"}) as start:
                    ReviewStore._write_json_atomic(service.command_path, {
                        "command_id": "manual-one", "action": "MANUAL_MISSED",
                    })
                    service._process_command()
                    self.assertTrue(service.last_result["ok"], service.last_result)
                    start.assert_called_once()
                    item = service.controller.store.get_queue_item(service.selected_event_id)
                    self.assertEqual(item["status"], "WAITING_CLIP")
                    os.utime(service.controller.status_path, (time.time() - 10,) * 2)
                    ReviewStore._write_json_atomic(service.command_path, {
                        "command_id": "manual-two", "action": "NEGATIVE_SAMPLE",
                    })
                    service._process_command()
                    self.assertFalse(service.last_result["ok"])
                    start.assert_called_once()
            finally:
                service.stop()

    def test_restart_does_not_replace_old_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = SafetyOfficerService(tmp)
            first.start()
            ready = first.controller.ingest_payload(snapshot())[0]
            first.controller.update_clip(ready["event_id"], {"status": "READY", "path": "original.mp4"})
            waiting = first.controller.store.enqueue_manual_sample(snapshot(), station_id="upper_station",
                sample_origin="MANUAL_MISSED", clip={"status": "RECORDING"})
            first.stop()
            second = SafetyOfficerService(tmp)
            second.start()
            try:
                store = second.controller.store
                self.assertEqual(store.get_queue_item(ready["event_id"])["clip"]["path"], "original.mp4")
                self.assertEqual(store.get_queue_item(waiting["event_id"])["clip"]["status"], "FAILED")
                self.assertIn(ready["event_id"], second._clip_started)
                self.assertIn(waiting["event_id"], second._clip_started)
            finally:
                second.stop()

    def test_audio_child_is_stopped_and_reaped(self):
        player = ReviewAudioPlayer()
        child = Mock()
        child.poll.return_value = None
        player.process = child
        player.stop()
        child.terminate.assert_called_once()
        child.wait.assert_called_once()
        self.assertIsNone(player.process)
        player.stop()

    def test_invalid_boolean_does_not_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewStore(Path(tmp) / "annotations", actuator_path=Path(tmp) / "actuator.json")
            item = store.ingest_snapshot(snapshot(), station_id="upper_station")
            store.update_clip(item["event_id"], {"status": "UNAVAILABLE"})
            with self.assertRaises(ContractError):
                store.submit_review(item["event_id"], review_level=4,
                    primary_action="VIOLATION", material_occluded="false")
            self.assertEqual(store.get_queue_item(item["event_id"])["status"], "PENDING_REVIEW")

    def test_stale_jpeg_is_not_added_to_evidence_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SafetyOfficerService(tmp)
            try:
                service.frame_path.parent.mkdir(parents=True, exist_ok=True)
                service.frame_path.write_bytes(b"old-jpeg")
                os.utime(service.frame_path, (time.time() - 60,) * 2)
                with patch.object(service.clips, "offer_jpeg") as offer:
                    service._offer_latest_frame(time.monotonic())
                    offer.assert_not_called()
            finally:
                service.clips.close()

    def test_serial_silence_preempts_pattern_and_shutdown_sends_silence(self):
        device = Mock()
        args = SimpleNamespace(max_age_s=1.5, heartbeat_s=0.5, dry_run=False,
            serial_port="fake-port", baudrate=115200, review_json="fake.json", once=False)
        samples = [
            (b"FATIGUE 4 3 80\n", "approved", "id-one", True, 80),
            (indicator_bridge.encode_silent_uncertain(), "cancelled", "id-two", False, 0),
        ]
        # Exit on the third poll; only mocked hardware, no real port opened.
        with patch.object(indicator_bridge, "parse_args", return_value=args), \
             patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=Mock(return_value=device))}), \
             patch.object(indicator_bridge, "reviewed_command_from_snapshot", side_effect=samples + [KeyboardInterrupt]), \
             patch.object(indicator_bridge.time, "sleep"), \
             patch.object(indicator_bridge.time, "monotonic", return_value=100.0):
            with self.assertRaises(KeyboardInterrupt):
                indicator_bridge.main()
        writes = [call.args[0] for call in device.write.call_args_list]
        self.assertEqual(writes, [samples[0][0], indicator_bridge.encode_silent_uncertain(),
            indicator_bridge.encode_silent_uncertain()])
        device.close.assert_called_once()

    def test_preflight_never_approves_unvalidated_deployment(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = inspect_project(Path(tmp))
            self.assertEqual(report["release_status"], "NOT_APPROVED_FOR_PRODUCTION")
            self.assertTrue(any(c["status"] == "BLOCKED" for c in report["checks"]))
