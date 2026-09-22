import subprocess
import sys
import unittest


class LightweightBridgeTests(unittest.TestCase):
    def test_json_actuator_workers_do_not_import_vision_runtime(self):
        result = subprocess.run([sys.executable, "-c",
            "import sys; import indicator_bridge, computer_buzzer_simulator; "
            "assert 'mediapipe' not in sys.modules; assert 'cv2' not in sys.modules"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_joint_engine_exports_still_work(self):
        from safety_monitor import JointState, JointRiskEngine
        self.assertEqual(JointState.NORMAL.name, "NORMAL")
        self.assertTrue(callable(JointRiskEngine))
