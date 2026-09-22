import unittest

from safety_monitor.config import RiskConfig
from safety_monitor.joint_engine import JointRiskEngine, JointState
from safety_monitor.machine_motion import MachineObservation, MachineState
from safety_monitor.pose_tracker import PoseObservation, PosePoint


def config() -> RiskConfig:
    return RiskConfig(
        danger_zone=((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)),
        warning_zone=((0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)),
        relevant_landmarks=(0,),
        enter_attention_frames=2,
        enter_warning_frames=2,
        enter_danger_frames=2,
        enter_uncertain_frames=2,
        clear_frames=2,
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


def pose(lean: float = 0.0) -> PoseObservation:
    landmarks = tuple(PosePoint(0.5, 0.5, 1.0, 1.0) for _ in range(33))
    return PoseObservation(
        landmarks=landmarks,
        shoulder_center=(0.55, 0.35),
        hip_center=(0.55, 0.7),
        torso_center=(0.55, 0.525),
        left_elbow_angle=120.0,
        right_elbow_angle=125.0,
        torso_lean_deg=lean,
        upper_body_visible=True,
        visibility_score=1.0,
    )


def machine(state: MachineState, quality: bool = True) -> MachineObservation:
    return MachineObservation(state, state, 1.0, 0.2, 0.0, quality, "test")


class JointEngineTests(unittest.TestCase):
    def test_missing_perception_becomes_uncertain(self) -> None:
        engine = JointRiskEngine(config())
        result = engine.update([], None, machine(MachineState.STATIC), 0.0)
        self.assertEqual(result.state, JointState.UNCERTAIN)
        self.assertEqual(result.raw_state, JointState.UNCERTAIN)

    def test_low_confidence_hand_does_not_claim_normal(self) -> None:
        engine = JointRiskEngine(config())
        result = engine.update(
            [[(0.2, 0.5)]],
            pose(),
            machine(MachineState.STATIC),
            0.0,
            hand_confidence=0.2,
        )
        self.assertEqual(result.raw_state, JointState.UNCERTAIN)

    def test_single_visible_hand_does_not_claim_normal(self) -> None:
        engine = JointRiskEngine(config())
        result = engine.update(
            [[(0.2, 0.5)]],
            pose(),
            machine(MachineState.STATIC),
            0.0,
            hand_confidence=0.9,
        )
        self.assertEqual(result.raw_state, JointState.UNCERTAIN)
        self.assertEqual(result.reason, "both_hands_not_reliably_visible")

    def test_pressing_hand_conflict_reaches_danger_after_debounce(self) -> None:
        engine = JointRiskEngine(config())
        engine.update([[(0.2, 0.5)]], pose(), machine(MachineState.STATIC), 0.0)
        engine.update([[(0.2, 0.5)]], pose(), machine(MachineState.STATIC), 0.1)
        first = engine.update([[(0.5, 0.5)]], pose(), machine(MachineState.PRESSING), 0.2)
        second = engine.update([[(0.5, 0.5)]], pose(), machine(MachineState.PRESSING), 0.3)
        self.assertEqual(first.raw_state, JointState.DANGER)
        self.assertEqual(second.state, JointState.DANGER)
        self.assertEqual(second.reason, "hand_and_pressing_motion_conflict")

    def test_unknown_machine_blocks_safety_claim(self) -> None:
        engine = JointRiskEngine(config())
        engine.update([[(0.2, 0.5)]], pose(), machine(MachineState.UNCERTAIN, False), 0.0)
        result = engine.update([[(0.2, 0.5)]], pose(), machine(MachineState.UNCERTAIN, False), 0.1)
        self.assertEqual(result.state, JointState.UNCERTAIN)

    def test_stream_fault_is_immediate(self) -> None:
        engine = JointRiskEngine(config())
        result = engine.fault(0.0)
        self.assertEqual(result.state, JointState.FAULT)

    def test_camera_alignment_issue_blocks_normal_claim(self) -> None:
        engine = JointRiskEngine(config())
        result = engine.update(
            [[(0.2, 0.5)], [(0.8, 0.5)]],
            pose(),
            machine(MachineState.STATIC),
            0.0,
            quality_issue="fixed_camera_alignment_changed",
        )
        self.assertEqual(result.raw_state, JointState.UNCERTAIN)
        self.assertEqual(result.reason, "fixed_camera_alignment_changed")

    def test_cached_pose_cannot_remain_safe_after_freshness_timeout(self) -> None:
        engine = JointRiskEngine(config())
        hands = [[(0.2, 0.5)], [(0.8, 0.5)]]
        first = engine.update(
            hands,
            pose(),
            machine(MachineState.STATIC),
            0.0,
            pose_updated=True,
        )
        stale = engine.update(
            hands,
            pose(),
            machine(MachineState.STATIC),
            0.2,
            pose_updated=False,
        )
        self.assertEqual(first.raw_state, JointState.NORMAL)
        self.assertEqual(stale.raw_state, JointState.UNCERTAIN)
        self.assertEqual(stale.reason, "hand_or_upper_body_not_reliably_visible")


if __name__ == "__main__":
    unittest.main()
