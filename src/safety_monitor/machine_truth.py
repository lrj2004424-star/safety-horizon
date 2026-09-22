"""Read-only machine-state evidence and conservative visual/truth fusion."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .config import MachineTruthConfig
from .machine_motion import MachineObservation, MachineState


@dataclass(frozen=True)
class MachineTruthObservation:
    state: MachineState
    quality_ok: bool
    age_s: float | None
    reason: str


class MachineTruthProvider:
    """Small interface for isolated, read-only machine-state evidence."""

    def update(self) -> MachineTruthObservation:
        raise NotImplementedError


class NullMachineTruthProvider(MachineTruthProvider):
    def update(self) -> MachineTruthObservation:
        return MachineTruthObservation(
            MachineState.UNCERTAIN,
            False,
            None,
            "machine_truth_not_configured",
        )


class JsonFileMachineTruthProvider(MachineTruthProvider):
    """Read a file written atomically by an electrically isolated gateway.

    The monitor never writes commands to this path. File freshness is checked from
    its modification time, so a stopped gateway immediately becomes UNCERTAIN.
    """

    def __init__(self, path: str | Path, max_age_s: float):
        self.path = Path(path)
        self.max_age_s = float(max_age_s)

    def update(self) -> MachineTruthObservation:
        try:
            stat = self.path.stat()
            age_s = max(0.0, time.time() - stat.st_mtime)
            if age_s > self.max_age_s:
                return MachineTruthObservation(
                    MachineState.UNCERTAIN,
                    False,
                    age_s,
                    "machine_truth_stale",
                )
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            state = MachineState(str(payload["state"]).upper())
            if state == MachineState.UNCERTAIN:
                return MachineTruthObservation(state, False, age_s, "machine_truth_uncertain")
            return MachineTruthObservation(state, True, age_s, "machine_truth_fresh")
        except FileNotFoundError:
            return MachineTruthObservation(
                MachineState.UNCERTAIN,
                False,
                None,
                "machine_truth_file_missing",
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError, OSError):
            return MachineTruthObservation(
                MachineState.UNCERTAIN,
                False,
                None,
                "machine_truth_invalid",
            )


def create_machine_truth_provider(
    config: MachineTruthConfig,
    resolved_path: str | Path | None,
) -> MachineTruthProvider:
    if config.mode == "json_file":
        if resolved_path is None:
            raise ValueError("machine_truth.path must resolve for json_file mode")
        return JsonFileMachineTruthProvider(resolved_path, config.max_age_s)
    return NullMachineTruthProvider()


def fuse_machine_observations(
    visual: MachineObservation,
    truth: MachineTruthObservation,
    *,
    required_for_danger: bool,
) -> MachineObservation:
    """Use truth for authorization and retain optical flow as an explanatory cue."""
    if truth.quality_ok:
        return MachineObservation(
            state=truth.state,
            raw_state=visual.raw_state,
            vertical_flow=visual.vertical_flow,
            moving_fraction=visual.moving_fraction,
            reference_motion=visual.reference_motion,
            quality_ok=True,
            reason=f"read_only_truth_{truth.state.value.lower()}",
        )
    if required_for_danger:
        return MachineObservation(
            state=MachineState.UNCERTAIN,
            raw_state=visual.raw_state,
            vertical_flow=visual.vertical_flow,
            moving_fraction=visual.moving_fraction,
            reference_motion=visual.reference_motion,
            quality_ok=False,
            reason=truth.reason,
        )
    return visual
