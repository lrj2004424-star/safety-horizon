"""Core package for the non-invasive cutting-station safety prototype."""

from .config import StationConfig, load_config
from .risk_engine import RiskAssessment, RiskEngine, RiskState


def __getattr__(name):
    # Serial/review workers import this package too. Loading the joint engine
    # eagerly pulls MediaPipe/OpenCV into workers that only exchange JSON.
    if name in {"JointAssessment", "JointRiskEngine", "JointState"}:
        from . import joint_engine
        value = getattr(joint_engine, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "RiskAssessment",
    "RiskEngine",
    "RiskState",
    "JointAssessment",
    "JointRiskEngine",
    "JointState",
    "StationConfig",
    "load_config",
]
