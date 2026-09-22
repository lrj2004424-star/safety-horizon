import tempfile
import os
import time
import sys
import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np

from safety_monitor.frame_activity import FrameActivityGuard
from safety_monitor.review_store import ReviewStore
from safety_officer_review import SafetyOfficerService
from supervise_service import WorkerSupervisor


class FrameActivityTests(unittest.TestCase):
    def test_frozen_image_times_out_and_motion_recovers(self):
        guard = FrameActivityGuard(timeout_s=12)
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        self.assertTrue(guard.observe(frame, 0))
        self.assertTrue(guard.observe(frame, 11.9))
        self.assertFalse(guard.observe(frame, 12))
        self.assertFalse(guard.observe(frame, 13))
        self.assertTrue(guard.observe(frame + 20, 14))

    def test_context_change_and_clock_reset_start_new_observation(self):
        guard = FrameActivityGuard(timeout_s=2)
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        guard.observe(frame, 10)
        self.assertFalse(guard.observe(frame, 12))
        self.assertTrue(guard.observe(frame, 13, context="TEST"))
        self.assertTrue(guard.observe(frame, 0, context="TEST"))

    def test_small_noise_cannot_keep_a_frozen_image_alive(self):
        guard = FrameActivityGuard(timeout_s=2)
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        guard.observe(frame, 0)
        frame[0, 0] = 1
        self.assertFalse(guard.observe(frame, 2))

    def test_invalid_configuration_rejected(self):
        for value in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                FrameActivityGuard(timeout_s=value)


class SupervisorTests(unittest.TestCase):
    def test_owned_real_child_is_reaped_on_shutdown(self):
        supervisor = WorkerSupervisor([sys.executable, "-c", "import time; time.sleep(60)"])
        supervisor.tick(0)
        child = supervisor.worker
        try:
            self.assertIsNone(child.poll())
        finally:
            supervisor.stop()
        self.assertIsNotNone(child.poll())

    def test_worker_not_duplicated_and_backoff_respected(self):
        child = Mock()
        child.poll.return_value = None
        spawn = Mock(return_value=child)
        supervisor = WorkerSupervisor(["worker"], spawn=spawn)
        supervisor.tick(0)
        supervisor.tick(0.5)
        spawn.assert_called_once()
        child.poll.return_value = 1
        supervisor.tick(1)
        supervisor.tick(2)
        self.assertEqual(spawn.call_count, 1)
        supervisor.tick(3)
        self.assertEqual(spawn.call_count, 2)

    def test_repeated_spawn_failure_opens_circuit(self):
        spawn = Mock(side_effect=OSError("unavailable"))
        supervisor = WorkerSupervisor(["worker"], spawn=spawn, limit=2)
        for moment in (0, 2, 6, 10, 1000):
            supervisor.tick(moment)
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(supervisor.state, "FAULT_RETRY_LIMIT")

    def test_stopping_worker_never_restarts_it(self):
        child = Mock()
        child.poll.return_value = None
        supervisor = WorkerSupervisor(["worker"], spawn=Mock(return_value=child))
        supervisor.tick(0)
        supervisor.stop()
        supervisor.tick(100)
        child.terminate.assert_called_once()
        child.wait.assert_called_once()
        self.assertEqual(supervisor.state, "STOPPED")
        self.assertEqual(supervisor.starts, 1)

    def test_review_restart_does_not_replay_unacknowledged_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SafetyOfficerService(tmp)
            ReviewStore._write_json_atomic(service.command_path, {
                "command_id": "old-unacknowledged", "action": "MANUAL_MISSED",
            })
            service.start()
            try:
                self.assertFalse(service.last_result["ok"])
                self.assertEqual(service.last_command_id, "old-unacknowledged")
                with patch.object(service.controller.store, "enqueue_manual_sample") as enqueue:
                    service._process_command()
                    enqueue.assert_not_called()
                self.assertEqual(service.controller.store.read_actuator()["action"], "SILENT")
            finally:
                service.stop()


class ReviewHealthDisplayTests(unittest.TestCase):
    def read_function(self, root):
        namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "touchdesigner/td_runtime.py"))
        function = namespace["_read_review_state"]
        function.__globals__["RUNTIME_DIR"] = root
        function.__globals__["REVIEW_STATE_PATH"] = root / "review.json"
        return function

    def test_old_running_state_is_shown_as_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            read = self.read_function(root)
            ReviewStore._write_json_atomic(root / "review.json", {"service_state": "RUNNING"})
            os.utime(root / "review.json", (time.time() - 20,) * 2)
            result = read()
            self.assertEqual(result["service_state"], "STALE")
            self.assertEqual(result["actuator"]["action"], "SILENT")

    def test_retry_limit_is_visible_even_with_old_worker_running_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            read = self.read_function(root)
            ReviewStore._write_json_atomic(root / "review.json", {"service_state": "RUNNING"})
            ReviewStore._write_json_atomic(root / "review-supervisor.json", {"state": "FAULT_RETRY_LIMIT"})
            result = read()
            self.assertEqual(result["service_state"], "FAULT_RETRY_LIMIT")
            self.assertFalse(result["last_result"]["ok"])
