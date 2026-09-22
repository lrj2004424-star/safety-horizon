import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from safety_monitor.machine_motion import MachineObservation, MachineState
from safety_monitor.machine_truth import (
    JsonFileMachineTruthProvider,
    MachineTruthObservation,
    fuse_machine_observations,
)


def visual(state: MachineState = MachineState.PRESSING) -> MachineObservation:
    return MachineObservation(state, state, 1.2, 0.3, 0.0, True, "visual")


class MachineTruthTests(unittest.TestCase):
    def test_fresh_json_state_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"state": "PRESSING"}), encoding="utf-8")
            result = JsonFileMachineTruthProvider(path, 1.0).update()
            self.assertTrue(result.quality_ok)
            self.assertEqual(result.state, MachineState.PRESSING)

    def test_stale_json_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"state": "STATIC"}), encoding="utf-8")
            old = time.time() - 4.0
            os.utime(path, (old, old))
            result = JsonFileMachineTruthProvider(path, 0.2).update()
            self.assertFalse(result.quality_ok)
            self.assertEqual(result.reason, "machine_truth_stale")

    def test_required_truth_blocks_visual_only_state(self) -> None:
        truth = MachineTruthObservation(
            MachineState.UNCERTAIN,
            False,
            None,
            "machine_truth_not_configured",
        )
        result = fuse_machine_observations(visual(), truth, required_for_danger=True)
        self.assertEqual(result.state, MachineState.UNCERTAIN)
        self.assertEqual(result.raw_state, MachineState.PRESSING)
        self.assertFalse(result.quality_ok)


if __name__ == "__main__":
    unittest.main()
