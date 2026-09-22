import unittest

from safety_monitor.config import RiskConfig
from safety_monitor.risk_engine import RiskEngine, RiskState


def config() -> RiskConfig:
    return RiskConfig(
        danger_zone=((0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)),
        warning_zone=((0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)),
        relevant_landmarks=(0,),
        enter_attention_frames=2,
        enter_warning_frames=2,
        enter_danger_frames=2,
        enter_uncertain_frames=2,
        clear_frames=3,
        prediction_horizon_s=0.5,
        approach_speed_threshold=0.2,
        speed_smoothing=1.0,
        danger_dwell_s=0.3,
        hand_missing_grace_s=0.2,
        pose_missing_grace_s=0.4,
        min_hand_confidence=0.5,
        forward_lean_change_deg=12.0,
        torso_approach_speed_threshold=0.08,
    )


def hand(x: float, y: float = 0.5) -> list[list[tuple[float, float]]]:
    return [[(x, y)]]


class RiskEngineTests(unittest.TestCase):
    def test_debounce_and_clear_hysteresis(self) -> None:
        engine = RiskEngine(config())
        self.assertEqual(engine.update(hand(0.2), 0.0).state, RiskState.CLEAR)
        self.assertEqual(engine.update(hand(0.35), 0.1).state, RiskState.CLEAR)
        self.assertEqual(engine.update(hand(0.35), 0.2).state, RiskState.WARNING)
        self.assertEqual(engine.update(hand(0.5), 0.3).state, RiskState.WARNING)
        self.assertEqual(engine.update(hand(0.5), 0.4).state, RiskState.DANGER)
        self.assertEqual(engine.update(hand(0.8), 0.5).state, RiskState.DANGER)
        self.assertEqual(engine.update(hand(0.8), 0.6).state, RiskState.DANGER)
        result = engine.update(hand(0.8), 0.7)
        self.assertEqual(result.state, RiskState.CLEAR)
        self.assertEqual(result.transition, (RiskState.DANGER, RiskState.CLEAR))

    def test_fast_approach_predicts_warning(self) -> None:
        engine = RiskEngine(config())
        engine.update(hand(0.1), 0.0)
        first = engine.update(hand(0.25), 0.1)
        second = engine.update(hand(0.29), 0.2)
        self.assertEqual(first.raw_state, RiskState.WARNING)
        self.assertEqual(first.reason, "trajectory_toward_danger_zone")
        self.assertEqual(second.state, RiskState.WARNING)

    def test_no_hand_does_not_claim_detection(self) -> None:
        engine = RiskEngine(config())
        result = engine.update([], 0.0)
        self.assertEqual(result.state, RiskState.CLEAR)
        self.assertEqual(result.reason, "no_hand_detected")
        self.assertEqual(result.hand_count, 0)


if __name__ == "__main__":
    unittest.main()
