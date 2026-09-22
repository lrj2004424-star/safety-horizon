"""Conservative, review-driven risk-score threshold recommendations.

Recommendations are always ``PROPOSED`` and cover score bands only.  This
module never edits the active OpenCV/fatigue configuration; activation must be
an explicit, separately audited operator action.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .review_contract import ContractError, alert_class_for, iso_utc, normalize_system_state, review_level


@dataclass(frozen=True)
class _Sample:
    score: int
    review_level: int
    state: str
    alert_class: str | None
    origin: str


def load_review_records(source: str | Path | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Load canonical records, a daily JSON/JSONL export, or an iterable."""
    if not isinstance(source, (str, Path)):
        return [dict(item) for item in source if isinstance(item, Mapping)]
    path = Path(source).expanduser().resolve()
    if path.is_dir():
        records: list[dict[str, Any]] = []
        # Prefer canonical records when present to avoid counting their daily
        # JSON/JSONL projections a second time.
        canonical = list((path / "_records").glob("*/*/*.json"))
        candidates = canonical or list(path.glob("*.json")) + list(path.glob("*.jsonl"))
        for candidate in candidates:
            records.extend(load_review_records(candidate))
        return records
    if path.suffix.lower() == ".jsonl":
        output = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ContractError(f"{path.name}:{line_number} is not an object")
            output.append(value)
        return output
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise ContractError(f"{path} does not contain review records")


class ThresholdOptimizer:
    def __init__(
        self,
        *,
        min_samples: int = 30,
        min_positive: int = 5,
        min_negative: int = 5,
        min_representative_samples: int = 5,
        target_recall: float = 0.90,
    ) -> None:
        if min_samples < 1 or min_positive < 1 or min_negative < 1:
            raise ValueError("sample minimums must be positive")
        if min_representative_samples < 1:
            raise ValueError("min_representative_samples must be positive")
        if not 0.0 < target_recall <= 1.0:
            raise ValueError("target_recall must be in (0, 1]")
        self.min_samples = int(min_samples)
        self.min_positive = int(min_positive)
        self.min_negative = int(min_negative)
        self.min_representative_samples = int(min_representative_samples)
        self.target_recall = float(target_recall)

    def analyze_confusion(
        self, records: str | Path | Iterable[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Report recorded system-vs-human outcomes without inventing accuracy."""
        values = load_review_records(records)
        counts = {
            "TRUE_POSITIVE": 0,
            "FALSE_POSITIVE": 0,
            "FALSE_NEGATIVE": 0,
            "TRUE_NEGATIVE": 0,
            "ABSTAIN_POSITIVE": 0,
            "ABSTAIN_NEGATIVE": 0,
        }
        representative = 0
        for record in values:
            classification = str((record.get("derived") or {}).get("confusion_class", ""))
            if classification in counts:
                counts[classification] += 1
            if str(record.get("sample_origin", "SYSTEM_ALERT")) in {"NEGATIVE_SAMPLE", "MANUAL_MISSED"}:
                representative += 1
        tp, fp, fn, tn = (
            counts["TRUE_POSITIVE"],
            counts["FALSE_POSITIVE"],
            counts["FALSE_NEGATIVE"],
            counts["TRUE_NEGATIVE"],
        )
        precision = tp / (tp + fp) if tp + fp else None
        accuracy_available = representative > 0 and tp + fp + fn + tn > 0
        accuracy = (tp + tn) / (tp + fp + fn + tn) if accuracy_available else None
        return {
            "counts": counts,
            "reviewed_alert_precision": _rounded(precision),
            "accuracy_available": accuracy_available,
            "accuracy": _rounded(accuracy),
            "accuracy_note": (
                "包含无报警抽检/漏报补录；需结合抽样覆盖率解释"
                if accuracy_available
                else "仅有报警审核样本，不宣称系统准确率"
            ),
            "representative_sample_count": representative,
        }

    def propose(
        self,
        records: str | Path | Iterable[Mapping[str, Any]],
        *,
        current_warning_min: int = 45,
        current_danger_min: int = 70,
        generated_at: datetime | None = None,
    ) -> dict[str, Any]:
        if not 1 <= current_warning_min < current_danger_min <= 100:
            raise ValueError("current thresholds must satisfy 1 <= warning < danger <= 100")
        values = load_review_records(records)
        field_values = [
            record
            for record in values
            if str(record.get("sample_origin", "SYSTEM_ALERT")).strip().upper()
            != "SIMULATION"
        ]
        simulation_excluded = len(values) - len(field_values)
        samples, rejected = self._samples(field_values)
        non_abstain = [sample for sample in samples if sample.alert_class != "UNCERTAIN"]
        positive = sum(sample.review_level >= 3 for sample in non_abstain)
        negative = len(non_abstain) - positive
        representative = sum(sample.origin in {"NEGATIVE_SAMPLE", "MANUAL_MISSED"} for sample in non_abstain)
        reasons: list[str] = []
        if len(non_abstain) < self.min_samples:
            reasons.append(f"有效样本 {len(non_abstain)} < {self.min_samples}")
        if positive < self.min_positive:
            reasons.append(f"3-5级样本 {positive} < {self.min_positive}")
        if negative < self.min_negative:
            reasons.append(f"1-2级样本 {negative} < {self.min_negative}")
        if representative < self.min_representative_samples:
            reasons.append(
                f"无报警抽检/漏报补录 {representative} < {self.min_representative_samples}"
            )
        counts = {
            "records_loaded": len(values),
            "field_records_used": len(field_values),
            "simulation_records_excluded": simulation_excluded,
            "valid_non_abstain": len(non_abstain),
            "human_positive": positive,
            "human_negative": negative,
            "representative": representative,
            "abstain": sum(sample.alert_class == "UNCERTAIN" for sample in samples),
            "rejected": rejected,
        }
        base: dict[str, Any] = {
            "schema_version": 1,
            "status": "NOT_READY" if reasons else "PROPOSED",
            "generated_at_utc": iso_utc(generated_at),
            "scope": "risk_score_bands_only",
            "auto_apply": False,
            "requires_explicit_approval": True,
            "current": {
                "warning_min": current_warning_min,
                "danger_min": current_danger_min,
            },
            "sample_counts": counts,
            "guardrails": {
                "target_recall": self.target_recall,
                "minimum_samples": self.min_samples,
                "minimum_human_positive": self.min_positive,
                "minimum_human_negative": self.min_negative,
                "minimum_representative_samples": self.min_representative_samples,
                "uncertain_excluded_from_threshold_fit": True,
            },
        }
        if reasons:
            base["reason"] = "；".join(reasons)
            base["suggested"] = None
            base["metrics"] = {
                "warning_before": self._metrics(non_abstain, current_warning_min, positive_level=3, accuracy_available=False),
                "danger_before": self._metrics(non_abstain, current_danger_min, positive_level=4, accuracy_available=False),
            }
            return base

        warning = self._best_threshold(non_abstain, positive_level=3)
        danger = self._best_threshold(non_abstain, positive_level=4)
        warning_min = warning["threshold"]
        danger_min = max(warning_min + 1, danger["threshold"])
        danger_min = min(100, danger_min)
        if danger_min <= warning_min:
            # A warning threshold of 100 leaves no valid danger band.  Keep the
            # current pair rather than emitting an impossible configuration.
            warning_min, danger_min = current_warning_min, current_danger_min
        base["suggested"] = {
            "warning_min": warning_min,
            "danger_min": danger_min,
        }
        base["reason"] = "基于人工审核标签生成候选分界；仅供批准，不会自动生效"
        base["metrics"] = {
            "warning_before": self._metrics(non_abstain, current_warning_min, positive_level=3, accuracy_available=True),
            "warning_after": self._metrics(non_abstain, warning_min, positive_level=3, accuracy_available=True),
            "danger_before": self._metrics(non_abstain, current_danger_min, positive_level=4, accuracy_available=True),
            "danger_after": self._metrics(non_abstain, danger_min, positive_level=4, accuracy_available=True),
        }
        return base

    def write_proposal(self, path: str | Path, proposal: Mapping[str, Any]) -> Path:
        """Atomically write only a proposal; ACTIVE is deliberately unsupported."""
        if proposal.get("status") != "PROPOSED":
            raise ContractError("only a ready PROPOSED recommendation may be written")
        if proposal.get("auto_apply") is not False or proposal.get("requires_explicit_approval") is not True:
            raise ContractError("proposal approval guardrails are missing")
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(dict(proposal), stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        return target

    @staticmethod
    def _samples(records: Sequence[Mapping[str, Any]]) -> tuple[list[_Sample], int]:
        output: list[_Sample] = []
        rejected = 0
        for record in records:
            try:
                system = record.get("system")
                human = record.get("human_review")
                if not isinstance(system, Mapping) or not isinstance(human, Mapping):
                    raise ContractError("record lacks system or human review")
                score_raw = system.get("risk_score")
                if isinstance(score_raw, bool) or not isinstance(score_raw, (int, float)):
                    raise ContractError("risk score is not numeric")
                score_float = float(score_raw)
                if not math.isfinite(score_float) or not 0 <= score_float <= 100:
                    raise ContractError("risk score is outside 0..100")
                state = normalize_system_state(system.get("state"))
                alert_class = alert_class_for(state, system.get("alert_class"))
                origin = str(record.get("sample_origin", "SYSTEM_ALERT")).strip().upper()
                output.append(
                    _Sample(
                        score=int(round(score_float)),
                        review_level=review_level(human.get("review_level")),
                        state=state,
                        alert_class=alert_class,
                        origin=origin,
                    )
                )
            except (ContractError, TypeError, ValueError):
                rejected += 1
        return output, rejected

    def _best_threshold(self, samples: Sequence[_Sample], *, positive_level: int) -> dict[str, Any]:
        candidates = [
            self._metrics(samples, threshold, positive_level=positive_level, accuracy_available=True)
            for threshold in range(1, 101)
        ]
        meeting_recall = [item for item in candidates if (item["recall"] or 0.0) >= self.target_recall]
        pool = meeting_recall or candidates
        # In a safety context, recall dominates.  Balanced accuracy and
        # precision break ties, followed by the higher threshold to reduce
        # nuisance alarms when the safety metrics are equivalent.
        return max(
            pool,
            key=lambda item: (
                item["recall"] or -1.0,
                item["balanced_accuracy"] or -1.0,
                item["precision"] or -1.0,
                item["threshold"],
            ),
        )

    @staticmethod
    def _metrics(
        samples: Sequence[_Sample],
        threshold: int,
        *,
        positive_level: int,
        accuracy_available: bool,
    ) -> dict[str, Any]:
        tp = fp = fn = tn = 0
        for sample in samples:
            expected = sample.review_level >= positive_level
            predicted = sample.score >= threshold
            if predicted and expected:
                tp += 1
            elif predicted:
                fp += 1
            elif expected:
                fn += 1
            else:
                tn += 1
        recall = tp / (tp + fn) if tp + fn else None
        precision = tp / (tp + fp) if tp + fp else None
        specificity = tn / (tn + fp) if tn + fp else None
        balanced = (
            (recall + specificity) / 2
            if recall is not None and specificity is not None
            else None
        )
        accuracy = (tp + tn) / (tp + fp + fn + tn) if accuracy_available and samples else None
        return {
            "threshold": threshold,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "recall": _rounded(recall),
            "precision": _rounded(precision),
            "specificity": _rounded(specificity),
            "balanced_accuracy": _rounded(balanced),
            "accuracy_available": accuracy_available,
            "accuracy": _rounded(accuracy),
        }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)
