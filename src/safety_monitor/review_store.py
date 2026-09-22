"""Crash-resistant local queue and annotation storage for safety review."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo
from .platform_runtime import lock_file, unlock_file

from .review_contract import (
    ContractError,
    QUEUE_SCHEMA_VERSION,
    SAMPLE_ORIGINS,
    actuator_is_authorized,
    alert_class_for,
    build_actuator_command,
    build_queue_item,
    build_review_record,
    build_silent_command,
    iso_utc,
    is_review_eligible,
    normalize_clip,
    normalize_system_state,
    parse_utc,
    safe_identifier,
    utc_now,
)


CSV_FIELDS = (
    "record_id",
    "event_id",
    "submitted_at_utc",
    "station_id",
    "sample_origin",
    "system_state",
    "alert_class",
    "system_score",
    "threshold_version",
    "review_level",
    "primary_action",
    "hand_zone",
    "material_occluded",
    "confusion_class",
    "buzzer_action",
    "clip_path",
    "notes",
)


class ReviewStore:
    """Persistent review queue with idempotent daily exports.

    Individual JSON records are canonical.  Human-readable daily JSON, JSONL,
    and CSV files are rebuilt atomically from those records, so an interrupted
    export cannot corrupt prior annotations.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        actuator_path: str | Path | None = None,
        timezone: str = "Asia/Shanghai",
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.queue_dir = self.root / "_queue"
        self.records_dir = self.root / "_records"
        self.state_dir = self.root / "_state"
        self.lock_path = self.root / ".review.lock"
        self.actuator_path = (
            Path(actuator_path).expanduser().resolve()
            if actuator_path is not None
            else self.root / "review-actuator.json"
        )
        self.timezone = ZoneInfo(timezone)
        self._now = now
        self._thread_lock = threading.RLock()
        for directory in (self.root, self.queue_dir, self.records_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            with self.lock_path.open("a+b") as lock:
                lock_file(lock)
                try:
                    yield
                finally:
                    unlock_file(lock)

    def ingest_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        station_id: str,
        clip: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Observe one inference snapshot and enqueue only a new risk episode.

        Repeated heartbeat snapshots do not create duplicate queue rows.  A
        return to a non-alert state rearms the station for its next episode.
        This method never writes the actuator file.
        """
        station = safe_identifier(station_id, field="station_id")
        state = self._state_for_station(snapshot, station)
        sample_origin = (
            "SIMULATION"
            if str(snapshot.get("input_mode", "LIVE")).strip().upper() == "TEST"
            else "SYSTEM_ALERT"
        )
        session_id = str(snapshot.get("session_id", "legacy-session")).strip() or "legacy-session"
        if session_id == "legacy-session":
            session_id = f"legacy-session:{str(snapshot.get('input_mode', 'LIVE')).strip().upper()}"
        alert_class = alert_class_for(state, self._alert_class_for_station(snapshot, station))
        with self._locked():
            episodes = self._read_json(self.state_dir / "episodes.json", default={})
            if not isinstance(episodes, dict):
                episodes = {}
            active = episodes.get(station)
            if not is_review_eligible(state):
                if active is not None:
                    episodes.pop(station, None)
                    self._write_json_atomic(self.state_dir / "episodes.json", episodes)
                return None
            if isinstance(active, dict) and active.get("session_id") == session_id:
                event_id = active.get("event_id")
                if event_id:
                    existing = self._load_queue_unlocked(str(event_id))
                    if existing is not None:
                        self._refresh_episode_item(existing, snapshot, station)
                        # A continuous WARNING episode that rises to DANGER is
                        # one incident, not two queue rows. Preserve its pre-roll
                        # and update the peak evidence until the worker returns
                        # to a non-alert state and rearms the station.
                        severity = {"UNCERTAIN": 0, "WARNING": 1, "DANGER": 2}
                        previous_class = str(existing.get("system", {}).get("alert_class", "UNCERTAIN"))
                        if severity.get(str(alert_class), 0) > severity.get(previous_class, 0):
                            station_payload = self._station_payload(snapshot, station)
                            system = existing.setdefault("system", {})
                            system["state"] = normalize_system_state(station_payload.get("state", snapshot.get("state")))
                            system["alert_class"] = alert_class
                            system["reason_code"] = str(station_payload.get("reason", snapshot.get("reason", "")))[:400]
                            active["alert_class"] = alert_class
                            episodes[station] = active
                            self._write_json_atomic(self.state_dir / "episodes.json", episodes)
                        self._write_queue_unlocked(existing)
                        return deepcopy(existing)
            item = build_queue_item(
                snapshot,
                station_id=station,
                clip=clip,
                created_at=created_at or self._now(),
                sample_origin=sample_origin,
            )
            self._write_queue_unlocked(item)
            episodes[station] = {
                "session_id": session_id,
                "alert_class": alert_class,
                "event_id": item["event_id"],
            }
            self._write_json_atomic(self.state_dir / "episodes.json", episodes)
            return deepcopy(item)

    def enqueue_manual_sample(
        self,
        snapshot: Mapping[str, Any],
        *,
        station_id: str,
        sample_origin: str,
        clip: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Add a missed-event or representative negative sample explicitly."""
        origin = str(sample_origin).strip().upper()
        if origin not in SAMPLE_ORIGINS - {"SYSTEM_ALERT"}:
            raise ContractError("manual samples require MANUAL_MISSED, NEGATIVE_SAMPLE, or SIMULATION")
        item = build_queue_item(
            snapshot,
            station_id=station_id,
            clip=clip,
            created_at=created_at or self._now(),
            sample_origin=origin,
        )
        with self._locked():
            self._write_queue_unlocked(item)
        return deepcopy(item)

    def get_queue_item(self, event_id: str) -> dict[str, Any] | None:
        with self._locked():
            item = self._load_queue_unlocked(event_id)
            return deepcopy(item) if item is not None else None

    def list_queue(
        self, *, statuses: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        accepted = {str(value).upper() for value in statuses} if statuses else None
        with self._locked():
            items = [
                item
                for path in self.queue_dir.glob("*.json")
                if (item := self._read_json(path, default=None)) is not None
                and isinstance(item, dict)
                and (accepted is None or item.get("status") in accepted)
            ]
        return sorted(items, key=lambda item: (str(item.get("created_at_utc", "")), str(item.get("event_id", ""))))

    def update_clip(
        self, event_id: str, clip: Mapping[str, Any], *, updated_at: datetime | None = None
    ) -> dict[str, Any]:
        normalized = normalize_clip(clip)
        with self._locked():
            item = self._require_queue_unlocked(event_id)
            if item.get("status") in {"SUBMITTED", "ACKNOWLEDGED"}:
                raise ContractError("a reviewed event clip may not be replaced")
            item["clip"] = normalized
            item["status"] = (
                "PENDING_REVIEW"
                if normalized["status"] in {"READY", "FAILED", "UNAVAILABLE"}
                else "WAITING_CLIP"
            )
            item["updated_at_utc"] = iso_utc(updated_at or self._now())
            self._write_queue_unlocked(item)
            return deepcopy(item)

    def begin_review(self, event_id: str, *, updated_at: datetime | None = None) -> dict[str, Any]:
        with self._locked():
            item = self._require_queue_unlocked(event_id)
            if item.get("status") not in {"PENDING_REVIEW", "IN_REVIEW"}:
                raise ContractError("event is not ready for review")
            item["status"] = "IN_REVIEW"
            item["updated_at_utc"] = iso_utc(updated_at or self._now())
            self._write_queue_unlocked(item)
            return deepcopy(item)

    def submit_review(
        self,
        event_id: str,
        *,
        review_level: int,
        primary_action: str,
        material_occluded: bool,
        hand_zone: str = "NOT_VISIBLE",
        frame_annotations: Sequence[Mapping[str, Any]] | None = None,
        notes: str = "",
        submitted_at: datetime | None = None,
        active_seconds: float = 2.2,
        allow_actuation: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Persist a human decision and atomically publish its sound action."""
        submitted = submitted_at or self._now()
        with self._locked():
            item = self._require_queue_unlocked(event_id)
            prior = item.get("review")
            if isinstance(prior, Mapping) and prior.get("record_id"):
                record = self._find_record_unlocked(str(prior["record_id"]))
                if record is None:
                    raise ContractError("queue references a missing review record")
                actuator = self._read_json(self.actuator_path, default=None)
                if not isinstance(actuator, dict) or actuator.get("decision_id") != record.get("record_id"):
                    # Retrying a completed submission must never renew permission
                    # or overwrite a different event's current actuator command.
                    actuator = build_silent_command(reason="duplicate_review_no_reauthorization", generated_at=submitted)
                return deepcopy(record), deepcopy(actuator)
            if item.get("status") not in {"PENDING_REVIEW", "IN_REVIEW"}:
                raise ContractError("event is not ready for review")
            if not isinstance(material_occluded, bool):
                raise ContractError("material_occluded must be a boolean")
            record = build_review_record(
                item,
                review_level_value=review_level,
                primary_action=primary_action,
                material_occluded=material_occluded,
                hand_zone=hand_zone,
                frame_annotations=frame_annotations,
                notes=notes,
                submitted_at=submitted,
            )
            if allow_actuation is not True:
                record["derived"]["buzzer_action"] = "SILENT"
                record["derived"]["actuation_inhibited"] = True
            # Validate/build before persisting: an invalid duration must not
            # leave a submitted record without a corresponding valid command.
            actuator = (
                build_actuator_command(record, generated_at=submitted, active_seconds=active_seconds)
                if allow_actuation is True
                else build_silent_command(reason="test_mode_or_invalid_mode_output_disabled", generated_at=submitted)
            )
            day = parse_utc(record["submitted_at_utc"], field="submitted_at_utc").astimezone(self.timezone).date()
            station = safe_identifier(record["station_id"], field="station_id")
            record_dir = self.records_dir / day.isoformat() / station
            record_dir.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(record_dir / f"{record['record_id']}.json", record)
            self._rebuild_daily_exports_unlocked(day, station)
            item["status"] = "SUBMITTED"
            item["updated_at_utc"] = iso_utc(submitted)
            item["review"] = {
                "record_id": record["record_id"],
                "submitted_at_utc": record["submitted_at_utc"],
                "review_level": record["human_review"]["review_level"],
                "confusion_class": record["derived"]["confusion_class"],
                "buzzer_action": record["derived"]["buzzer_action"],
            }
            self._write_queue_unlocked(item)
            self._write_json_atomic(self.actuator_path, actuator)
            return deepcopy(record), deepcopy(actuator)

    def acknowledge(self, event_id: str, *, acknowledged_at: datetime | None = None) -> dict[str, Any]:
        moment = acknowledged_at or self._now()
        with self._locked():
            item = self._require_queue_unlocked(event_id)
            if item.get("status") not in {"SUBMITTED", "ACKNOWLEDGED"}:
                raise ContractError("only a submitted review may be acknowledged")
            item["status"] = "ACKNOWLEDGED"
            item["updated_at_utc"] = iso_utc(moment)
            self._write_queue_unlocked(item)
            silent = build_silent_command(reason="review_acknowledged", generated_at=moment)
            self._write_json_atomic(self.actuator_path, silent)
            return deepcopy(silent)

    def publish_silent(self, reason: str, *, generated_at: datetime | None = None) -> dict[str, Any]:
        command = build_silent_command(reason=reason, generated_at=generated_at or self._now())
        with self._locked():
            self._write_json_atomic(self.actuator_path, command)
        return command

    def read_actuator(self) -> dict[str, Any] | None:
        with self._locked():
            payload = self._read_json(self.actuator_path, default=None)
            return deepcopy(payload) if isinstance(payload, dict) else None

    def expire_actuator(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        moment = now or self._now()
        with self._locked():
            payload = self._read_json(self.actuator_path, default=None)
            if not isinstance(payload, dict) or payload.get("action") != "ALERT":
                return deepcopy(payload) if isinstance(payload, dict) else None
            if actuator_is_authorized(payload, now=moment):
                return deepcopy(payload)
            silent = build_silent_command(reason="review_command_expired", generated_at=moment)
            self._write_json_atomic(self.actuator_path, silent)
            return deepcopy(silent)

    def records(
        self, *, day: date | str | None = None, station_id: str | None = None
    ) -> list[dict[str, Any]]:
        station = safe_identifier(station_id, field="station_id") if station_id else None
        if isinstance(day, str):
            try:
                target_day = date.fromisoformat(day)
            except ValueError as exc:
                raise ContractError("day must use YYYY-MM-DD") from exc
        else:
            target_day = day
        paths: Iterable[Path]
        if target_day is not None and station is not None:
            paths = (self.records_dir / target_day.isoformat() / station).glob("*.json")
        elif target_day is not None:
            paths = (self.records_dir / target_day.isoformat()).glob("*/*.json")
        elif station is not None:
            paths = self.records_dir.glob(f"*/{station}/*.json")
        else:
            paths = self.records_dir.glob("*/*/*.json")
        with self._locked():
            values = [value for path in paths if isinstance((value := self._read_json(path, default=None)), dict)]
        return sorted(values, key=lambda item: str(item.get("submitted_at_utc", "")))

    def statistics(
        self, *, day: date | str | None = None, station_id: str | None = None
    ) -> dict[str, Any]:
        all_records = self.records(day=day, station_id=station_id)
        # Labelled simulations have their own test log and must not inflate or
        # reduce field accuracy/false-alarm statistics.
        records = [
            record
            for record in all_records
            if str(record.get("sample_origin", "SYSTEM_ALERT")).upper() != "SIMULATION"
        ]
        simulation_count = len(all_records) - len(records)
        confusion = {
            key: 0
            for key in (
                "TRUE_POSITIVE",
                "FALSE_POSITIVE",
                "FALSE_NEGATIVE",
                "TRUE_NEGATIVE",
                "ABSTAIN_POSITIVE",
                "ABSTAIN_NEGATIVE",
            )
        }
        levels = {str(level): 0 for level in range(1, 6)}
        representative = 0
        for record in records:
            derived = record.get("derived", {})
            classification = str(derived.get("confusion_class", ""))
            if classification in confusion:
                confusion[classification] += 1
            human = record.get("human_review", {})
            level = human.get("review_level")
            if isinstance(level, int) and str(level) in levels:
                levels[str(level)] += 1
            if str(record.get("sample_origin", "SYSTEM_ALERT")) in {"NEGATIVE_SAMPLE", "MANUAL_MISSED"}:
                representative += 1
        tp, fp = confusion["TRUE_POSITIVE"], confusion["FALSE_POSITIVE"]
        fn, tn = confusion["FALSE_NEGATIVE"], confusion["TRUE_NEGATIVE"]
        precision = tp / (tp + fp) if tp + fp else None
        accuracy_available = representative > 0 and (tp + fp + fn + tn) > 0
        accuracy = (tp + tn) / (tp + fp + fn + tn) if accuracy_available else None
        recall_available = any(
            str(record.get("sample_origin", "SYSTEM_ALERT")) in {"MANUAL_MISSED", "NEGATIVE_SAMPLE"}
            for record in records
        ) and tp + fn > 0
        recall = tp / (tp + fn) if recall_available else None
        pending = len(self.list_queue(statuses={"WAITING_CLIP", "PENDING_REVIEW", "IN_REVIEW"}))
        return {
            "schema_version": 1,
            "generated_at_utc": iso_utc(self._now()),
            "reviewed_count": len(records),
            "simulation_reviewed_count": simulation_count,
            "pending_count": pending,
            "valid_alarm_count": sum(1 for record in records if record.get("derived", {}).get("human_positive") is True),
            "false_alarm_count": fp,
            "level_distribution": levels,
            "confusion": confusion,
            "reviewed_alert_precision": round(precision, 4) if precision is not None else None,
            "accuracy_available": accuracy_available,
            "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "accuracy_note": (
                "包含无报警抽检/漏报补录，准确率仍应结合抽样覆盖率解释"
                if accuracy_available
                else "尚无无报警抽检或漏报补录，不宣称系统准确率"
            ),
            "recall_available": recall_available,
            "recall": round(recall, 4) if recall is not None else None,
            "representative_sample_count": representative,
        }

    def _state_for_station(self, snapshot: Mapping[str, Any], station_id: str) -> str:
        stations = snapshot.get("stations")
        if isinstance(stations, list):
            for station in stations:
                if isinstance(station, Mapping) and station.get("station_id") == station_id:
                    return normalize_system_state(station.get("state"))
        return normalize_system_state(snapshot.get("state"))

    @staticmethod
    def _station_payload(snapshot: Mapping[str, Any], station_id: str) -> Mapping[str, Any]:
        stations = snapshot.get("stations")
        if isinstance(stations, list):
            for station in stations:
                if isinstance(station, Mapping) and station.get("station_id") == station_id:
                    return station
        return snapshot

    def _alert_class_for_station(self, snapshot: Mapping[str, Any], station_id: str) -> Any:
        stations = snapshot.get("stations")
        if isinstance(stations, list):
            for station in stations:
                if isinstance(station, Mapping) and station.get("station_id") == station_id:
                    return station.get("alert_class")
        return snapshot.get("alert_class")

    def _refresh_episode_item(
        self, item: dict[str, Any], snapshot: Mapping[str, Any], station_id: str
    ) -> None:
        station_payload: Mapping[str, Any] = snapshot
        stations = snapshot.get("stations")
        if isinstance(stations, list):
            for station in stations:
                if isinstance(station, Mapping) and station.get("station_id") == station_id:
                    station_payload = station
                    break
        raw_score = station_payload.get(
            "risk_score", station_payload.get("score", snapshot.get("risk_score", snapshot.get("score", 0)))
        )
        try:
            score = int(round(float(raw_score)))
        except (TypeError, ValueError):
            score = int(item.get("system", {}).get("risk_score", 0))
        score = max(0, min(100, score))
        system = item.setdefault("system", {})
        system["latest_risk_score"] = score
        system["peak_risk_score"] = max(score, int(system.get("peak_risk_score", system.get("risk_score", 0))))
        item["updated_at_utc"] = iso_utc(self._now())

    def _queue_path(self, event_id: str) -> Path:
        event = safe_identifier(event_id, field="event_id")
        return self.queue_dir / f"{event}.json"

    def _write_queue_unlocked(self, item: Mapping[str, Any]) -> None:
        if item.get("schema_version") != QUEUE_SCHEMA_VERSION:
            raise ContractError("queue item schema is invalid")
        self._write_json_atomic(self._queue_path(str(item.get("event_id"))), item)

    def _load_queue_unlocked(self, event_id: str) -> dict[str, Any] | None:
        payload = self._read_json(self._queue_path(event_id), default=None)
        return payload if isinstance(payload, dict) else None

    def _require_queue_unlocked(self, event_id: str) -> dict[str, Any]:
        item = self._load_queue_unlocked(event_id)
        if item is None:
            raise ContractError("review event does not exist")
        return item

    def _find_record_unlocked(self, record_id: str) -> dict[str, Any] | None:
        safe_identifier(record_id, field="record_id")
        matches = list(self.records_dir.glob(f"*/*/{record_id}.json"))
        if not matches:
            return None
        payload = self._read_json(matches[0], default=None)
        return payload if isinstance(payload, dict) else None

    def _records_for_day_station_unlocked(self, day: date, station_id: str) -> list[dict[str, Any]]:
        directory = self.records_dir / day.isoformat() / station_id
        values = [
            value
            for path in directory.glob("*.json")
            if isinstance((value := self._read_json(path, default=None)), dict)
        ]
        return sorted(values, key=lambda item: str(item.get("submitted_at_utc", "")))

    def _rebuild_daily_exports_unlocked(self, day: date, station_id: str) -> None:
        records = self._records_for_day_station_unlocked(day, station_id)
        output_dir = self.root / day.isoformat()
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = output_dir / station_id
        self._write_json_atomic(stem.with_suffix(".json"), records)
        jsonl = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
        self._write_text_atomic(stem.with_suffix(".jsonl"), jsonl)
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(self._csv_row(record))
        self._write_text_atomic(stem.with_suffix(".csv"), buffer.getvalue())

    @staticmethod
    def _csv_row(record: Mapping[str, Any]) -> dict[str, Any]:
        system = record.get("system", {})
        human = record.get("human_review", {})
        derived = record.get("derived", {})
        clip = record.get("clip", {})
        return {
            "record_id": record.get("record_id"),
            "event_id": record.get("event_id"),
            "submitted_at_utc": record.get("submitted_at_utc"),
            "station_id": record.get("station_id"),
            "sample_origin": record.get("sample_origin"),
            "system_state": system.get("state"),
            "alert_class": system.get("alert_class"),
            "system_score": system.get("risk_score"),
            "threshold_version": system.get("threshold_version"),
            "review_level": human.get("review_level"),
            "primary_action": human.get("primary_action"),
            "hand_zone": human.get("hand_zone"),
            "material_occluded": human.get("material_occluded"),
            "confusion_class": derived.get("confusion_class"),
            "buzzer_action": derived.get("buzzer_action"),
            "clip_path": clip.get("path"),
            "notes": human.get("notes"),
        }

    @staticmethod
    def _read_json(path: Path, *, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        ReviewStore._write_text_atomic(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
