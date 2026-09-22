"""Release-only integration tests / 发布接口回归；仅临时模拟数据，不触发硬件。"""
from pathlib import Path
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from web_portal import Portal
from safety_officer_review import SafetyOfficerService
from safety_monitor.review_store import ReviewStore


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name)
        (self.src / "runtime").mkdir()
        self.portal = Portal(self.src, port=0)
        self.portal.start(open_browser=False)

    def tearDown(self):
        self.portal.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        h = {"X-Safety-Token": self.portal.token, "Origin": self.portal.origin}
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.portal.server.server_port, timeout=8)
        c.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=h)
        r = c.getresponse()
        status, data = r.status, r.read()
        c.close()
        return status, data

    def test_no_auth_no_private_state(self):
        self.assertEqual(self.request("GET", "/api/state", headers={"X-Safety-Token": ""})[0], 403)

    def test_foreign_origin_and_host_rejected(self):
        self.assertEqual(self.request("POST", "/api/command", {"action": "ACKNOWLEDGE"}, {"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "evil.test"})[0], 403)

    def test_no_arbitrary_commands_or_paths(self):
        self.assertEqual(self.request("POST", "/api/command", {"action": "EXEC"})[0], 400)
        self.assertEqual(self.request("GET", "/api/frame?event=../../etc/passwd")[0], 404)

    def test_stale_state_and_image_fail_visible(self):
        ReviewStore._write_json_atomic(self.src / "runtime/safety-officer-review-state.json", {"service_state": "RUNNING"})
        os.utime(self.src / "runtime/safety-officer-review-state.json", (1, 1))
        p = json.loads(self.request("GET", "/api/state")[1])
        self.assertEqual(p["service_state"], "STALE")
        self.assertEqual(self.request("GET", "/api/image")[0], 503)
        self.assertEqual(self.request("POST", "/api/command", {"action": "ACKNOWLEDGE"})[0], 503)

    def test_review_roundtrip_and_persistence_is_silent_in_test(self):
        service = SafetyOfficerService(self.src)
        ReviewStore._write_json_atomic(service.input_mode_path, {"mode": "TEST", "physical_actuator_allowed": False})
        service.start()
        payload = {"schema_version": 3, "session_id": "synthetic", "sequence": 1,
                   "state": "HIGH_RISK", "risk_score": 82, "reason": "synthetic_test", "input_mode": "TEST",
                   "stations": [{"station_id": "upper_station", "state": "HIGH_RISK", "risk_score": 82}]}
        event = service.controller.ingest_payload(payload)[0]
        service.controller.update_clip(event["event_id"], {"status": "UNAVAILABLE"})
        service.publish_state()
        stop = threading.Event()
        def worker():
            while not stop.wait(.02):
                service._process_command()
                service.publish_state()
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            command = {"action": "SUBMIT_REVIEW", "event_id": event["event_id"], "review_level": 5,
                       "primary_action": "VIOLATION", "hand_zone": "DANGER", "material_occluded": False}
            code, body = self.request("POST", "/api/command", command)
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)["ok"])
            self.assertEqual(service.controller.store.read_actuator()["action"], "SILENT")
            records = list((self.src / "annotations/_records").rglob("*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0].read_text())["human_review"]["review_level"], 5)
            self.request("POST", "/api/command", command)
            self.assertEqual(len(list((self.src / "annotations/_records").rglob("*.json"))), 1)
        finally:
            stop.set()
            thread.join(3)
            service.stop()

    def test_frame_decode_and_escape_rejection(self):
        import cv2
        import numpy as np
        folder = self.src / "annotations"
        folder.mkdir()
        clip = folder / "synthetic.avi"
        writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 8, (64, 48))
        self.assertTrue(writer.isOpened())
        for i in range(8):
            writer.write(np.full((48, 64, 3), i * 20, dtype=np.uint8))
        writer.release()
        state_path = self.src / "runtime/safety-officer-review-state.json"
        ReviewStore._write_json_atomic(state_path, {"queue": [{"event_id": "synthetic", "clip_path": str(clip)}]})
        status, body = self.request("GET", "/api/frame?event=synthetic&index=3")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["frames"], 8)
        self.assertEqual(self.request("GET", "/api/frame?event=synthetic&index=99")[0], 404)
        ReviewStore._write_json_atomic(state_path, {"queue": [{"event_id": "synthetic", "clip_path": "/etc/passwd"}]})
        self.assertEqual(self.request("GET", "/api/frame?event=synthetic")[0], 404)

    def test_pending_external_command_not_overwritten(self):
        ReviewStore._write_json_atomic(self.src / "runtime/safety-officer-review-state.json", {"service_state": "RUNNING", "last_command_id": "old"})
        path = self.src / "runtime/safety-officer-review-command.json"
        ReviewStore._write_json_atomic(path, {"command_id": "pending"})
        self.assertEqual(self.request("POST", "/api/command", {"action": "ACKNOWLEDGE"})[0], 409)
        self.assertEqual(json.loads(path.read_text())["command_id"], "pending")


class PackageTests(unittest.TestCase):
    def test_builder_never_destroys_existing_graph(self):
        code = (ROOT / "src/touchdesigner/build_touchdesigner_project.py").read_text()
        self.assertNotIn("existing.destroy()", code)
        self.assertIn("Use a NEW blank TD project", code)
        self.assertNotIn("/Users/", code)

    def test_default_alerts_are_off(self):
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "horizon.py"), "run", "--no-vision", "--alerts", "computer"], capture_output=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"cannot start audible", r.stderr)


if __name__ == "__main__":
    unittest.main()
