"""Validated data contracts for the safety-officer review workflow.

The vision result is evidence, not an actuator command.  Only a submitted
human review may produce a ``review-actuator.json`` payload.  Keeping this
boundary in a small dependency-free module lets both TouchDesigner and a
standalone sidecar use exactly the same rules.
"""

from __future__ import annotations

import math
import re
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence


SYSTEM_POSITIVE_STATES = frozenset({"FATIGUE_TREND", "HIGH_RISK", "WARNING", "DANGER"})
REVIEW_ELIGIBLE_STATES = SYSTEM_POSITIVE_STATES | {"UNCERTAIN"}
SYSTEM_NEGATIVE_STATES = frozenset({"ACTIVE", "OBSERVING", "NORMAL", "ATTENTION", "CLEAR"})

ALERT_CLASSES = frozenset({"WARNING", "DANGER", "UNCERTAIN"})
QUEUE_STATUSES = frozenset(
    {"WAITING_CLIP", "PENDING_REVIEW", "IN_REVIEW", "SUBMITTED", "ACKNOWLEDGED"}
)
CLIP_STATUSES = frozenset({"PENDING", "RECORDING", "READY", "FAILED", "UNAVAILABLE"})
HAND_ZONES = frozenset({"SAFE", "CAUTION", "DANGER", "NOT_VISIBLE"})
HAND_SIDES = frozenset({"LEFT", "RIGHT", "UNKNOWN"})
PRIMARY_ACTIONS = frozenset(
    {"NORMAL_PICK", "WASTE_CLEANUP", "VIOLATION", "STYLE_CHANGE", "OTHER"}
)
SAMPLE_ORIGINS = frozenset(
    {"SYSTEM_ALERT", "MANUAL_MISSED", "NEGATIVE_SAMPLE", "SIMULATION"}
)
CONFUSION_CLASSES = frozenset(
    {
        "TRUE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_NEGATIVE",
        "TRUE_NEGATIVE",
        "ABSTAIN_POSITIVE",
        "ABSTAIN_NEGATIVE",
    }
)

ACTUATOR_SCHEMA_VERSION = 1
QUEUE_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
TWO_SHORT_ONE_LONG = "TWO_SHORT_ONE_LONG"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ContractError(ValueError):
    """Raised when external review data does not satisfy the contract."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | None = None) -> str:
    moment = value or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty UTC timestamp")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError as exc:
        raise ContractError(f"{field} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def new_id(prefix: str) -> str:
    safe = safe_identifier(prefix, field="id prefix")
    return f"{safe}_{uuid.uuid4().hex}"


def safe_identifier(value: Any, *, field: str = "identifier") -> str:
    result = str(value or "").strip()
    if not _SAFE_ID.fullmatch(result):
        raise ContractError(f"{field} contains unsupported characters")
    return result


def bounded_score(value: Any, *, field: str = "risk_score") -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{field} must be numeric")
    if not math.isfinite(float(value)):
        raise ContractError(f"{field} must be finite")
    rounded = int(round(float(value)))
    if not 0 <= rounded <= 100:
        raise ContractError(f"{field} must be between 0 and 100")
    return rounded


def review_level(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise ContractError("review_level must be an integer from 1 to 5")
    return value


def normalize_system_state(value: Any) -> str:
    state = str(value or "").strip().upper()
    if state not in REVIEW_ELIGIBLE_STATES | SYSTEM_NEGATIVE_STATES | {"FAULT"}:
        raise ContractError(f"unsupported system state: {state or '<empty>'}")
    return state


def alert_class_for(state: Any, explicit: Any = None) -> str | None:
    normalized = normalize_system_state(state)
    if explicit is not None:
        result = str(explicit).strip().upper()
        if result not in ALERT_CLASSES:
            raise ContractError("alert_class must be WARNING, DANGER, or UNCERTAIN")
        expected = alert_class_for(normalized)
        if expected is not None and result != expected:
            raise ContractError("alert_class conflicts with system state")
        return result
    if normalized in {"FATIGUE_TREND", "WARNING"}:
        return "WARNING"
    if normalized in {"HIGH_RISK", "DANGER"}:
        return "DANGER"
    if normalized == "UNCERTAIN":
        return "UNCERTAIN"
    return None


def is_review_eligible(state: Any) -> bool:
    try:
        return normalize_system_state(state) in REVIEW_ELIGIBLE_STATES
    except ContractError:
        return False


def is_human_positive(level: Any) -> bool:
    return review_level(level) >= 3


def confusion_class(
    *, system_state: Any, review_level_value: Any, alert_class: Any = None
) -> str:
    state = normalize_system_state(system_state)
    positive = is_human_positive(review_level_value)
    normalized_alert = alert_class_for(state, alert_class)
    if normalized_alert == "UNCERTAIN":
        return "ABSTAIN_POSITIVE" if positive else "ABSTAIN_NEGATIVE"
    if state in SYSTEM_POSITIVE_STATES:
        return "TRUE_POSITIVE" if positive else "FALSE_POSITIVE"
    if state in SYSTEM_NEGATIVE_STATES:
        return "FALSE_NEGATIVE" if positive else "TRUE_NEGATIVE"
    # FAULT represents a system failure, not a prediction.  Treat it as an
    # abstention so it cannot improve or damage model accuracy by accident.
    return "ABSTAIN_POSITIVE" if positive else "ABSTAIN_NEGATIVE"


def _station_payload(snapshot: Mapping[str, Any], station_id: str) -> Mapping[str, Any]:
    stations = snapshot.get("stations")
    if isinstance(stations, Sequence) and not isinstance(stations, (str, bytes)):
        for station in stations:
            if isinstance(station, Mapping) and str(station.get("station_id", "")) == station_id:
                return station
    return snapshot


def _feature_snapshot(station: Mapping[str, Any]) -> dict[str, Any]:
    people = station.get("people")
    candidates = [person for person in people or [] if isinstance(person, Mapping)] if isinstance(people, list) else []
    if not candidates:
        return {"visible_people": 0}
    person = max(candidates, key=lambda item: float(item.get("risk_score", item.get("score", 0)) or 0))
    result: dict[str, Any] = {"visible_people": len(candidates)}
    for key in (
        "track_id",
        "observed_seconds",
        "head_drop_change",
        "lean_change_deg",
        "shoulder_tilt_change_deg",
        "motion_rate",
        "posture_signal_count",
        "body_visibility",
    ):
        value = person.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            result[key] = value
    return result


def normalize_clip(clip: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(clip or {})
    status = str(raw.get("status", "PENDING")).strip().upper()
    if status not in CLIP_STATUSES:
        raise ContractError(f"unsupported clip status: {status}")
    pre_roll = float(raw.get("pre_roll_s", 3.0))
    post_roll = float(raw.get("post_roll_s", 5.0))
    fps = float(raw.get("fps", 20.0))
    if not 0.0 <= pre_roll <= 10.0 or not 0.0 <= post_roll <= 30.0 or fps <= 0.0:
        raise ContractError("clip timing values are invalid")
    path_value = raw.get("path")
    path = None if path_value in (None, "") else str(path_value)
    if path is not None and ".." in Path(path).parts:
        raise ContractError("clip path may not traverse parent directories")
    result: dict[str, Any] = {
        "status": status,
        "path": path,
        "pre_roll_s": round(pre_roll, 3),
        "post_roll_s": round(post_roll, 3),
        "fps": round(fps, 3),
        "face_blurred": bool(raw.get("face_blurred", True)),
        "contains_identity": False,
    }
    for key in ("frame_count", "width", "height", "trigger_frame_index"):
        value = raw.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError(f"clip.{key} must be a non-negative integer")
            result[key] = value
    if raw.get("error"):
        result["error"] = str(raw["error"])[:240]
    return result


def build_queue_item(
    snapshot: Mapping[str, Any],
    *,
    station_id: str,
    event_id: str | None = None,
    created_at: datetime | None = None,
    clip: Mapping[str, Any] | None = None,
    sample_origin: str = "SYSTEM_ALERT",
) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise ContractError("status snapshot must be an object")
    station = safe_identifier(station_id, field="station_id")
    station_status = _station_payload(snapshot, station)
    state = normalize_system_state(station_status.get("state", snapshot.get("state")))
    if not is_review_eligible(state) and sample_origin == "SYSTEM_ALERT":
        raise ContractError("system-alert queue items require a review-eligible state")
    alert_class = alert_class_for(
        state,
        station_status.get("alert_class", snapshot.get("alert_class")),
    )
    score = bounded_score(
        station_status.get(
            "risk_score",
            station_status.get("score", snapshot.get("risk_score", snapshot.get("score", 0))),
        )
    )
    origin = str(sample_origin).strip().upper()
    if origin not in SAMPLE_ORIGINS:
        raise ContractError(f"unsupported sample_origin: {origin}")
    clip_data = normalize_clip(clip)
    queue_status = "PENDING_REVIEW" if clip_data["status"] in {"READY", "UNAVAILABLE", "FAILED"} else "WAITING_CLIP"
    sequence = snapshot.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        sequence = None
    session_id = str(snapshot.get("session_id", "legacy-session")).strip() or "legacy-session"
    if session_id != "legacy-session":
        session_id = safe_identifier(session_id, field="session_id")
    reason = str(station_status.get("reason", snapshot.get("reason", "")))[:400]
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "event_id": safe_identifier(event_id, field="event_id") if event_id else new_id("evt"),
        "created_at_utc": iso_utc(created_at),
        "updated_at_utc": iso_utc(created_at),
        "station_id": station,
        "sample_origin": origin,
        "status": queue_status,
        "system": {
            "session_id": session_id,
            "source_sequence": sequence,
            "timestamp_utc": snapshot.get("timestamp_utc"),
            "timestamp_monotonic_s": snapshot.get("timestamp_monotonic_s"),
            "state": state,
            "alert_class": alert_class,
            "risk_score": score,
            "reason_code": reason,
            "threshold_version": str(snapshot.get("threshold_version", "legacy-v1")),
            "features": _feature_snapshot(station_status),
        },
        "clip": clip_data,
        "review": None,
        "advisory_only": True,
        "contains_identity": False,
        "machine_control_enabled": False,
    }


def _normalize_frame_annotations(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ContractError("frame_annotations must be a list")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ContractError(f"frame_annotations[{index}] must be an object")
        frame_index = item.get("frame_index")
        offset_ms = item.get("offset_ms")
        if isinstance(frame_index, bool) or not isinstance(frame_index, int) or frame_index < 0:
            raise ContractError(f"frame_annotations[{index}].frame_index is invalid")
        if isinstance(offset_ms, bool) or not isinstance(offset_ms, int) or offset_ms < 0:
            raise ContractError(f"frame_annotations[{index}].offset_ms is invalid")
        hands_raw = item.get("hands", [])
        if not isinstance(hands_raw, Sequence) or isinstance(hands_raw, (str, bytes)):
            raise ContractError(f"frame_annotations[{index}].hands must be a list")
        hands: list[dict[str, str]] = []
        for hand in hands_raw:
            if not isinstance(hand, Mapping):
                raise ContractError("each annotated hand must be an object")
            side = str(hand.get("side", "UNKNOWN")).strip().upper()
            zone = str(hand.get("zone", "NOT_VISIBLE")).strip().upper()
            if side not in HAND_SIDES or zone not in HAND_ZONES:
                raise ContractError("annotated hand side or zone is invalid")
            hands.append({"side": side, "zone": zone})
        output.append(
            {
                "frame_index": frame_index,
                "offset_ms": offset_ms,
                "hands": hands,
                "material_occluded": bool(item.get("material_occluded", False)),
            }
        )
    return output


def build_review_record(
    queue_item: Mapping[str, Any],
    *,
    review_level_value: int,
    primary_action: str,
    material_occluded: bool,
    hand_zone: str = "NOT_VISIBLE",
    frame_annotations: Sequence[Mapping[str, Any]] | None = None,
    notes: str = "",
    submitted_at: datetime | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(queue_item, Mapping) or queue_item.get("schema_version") != QUEUE_SCHEMA_VERSION:
        raise ContractError("queue item schema is invalid")
    event_id = safe_identifier(queue_item.get("event_id"), field="event_id")
    station_id = safe_identifier(queue_item.get("station_id"), field="station_id")
    system = queue_item.get("system")
    if not isinstance(system, Mapping):
        raise ContractError("queue item is missing system evidence")
    level = review_level(review_level_value)
    action = str(primary_action).strip().upper()
    if action not in PRIMARY_ACTIONS:
        raise ContractError(f"unsupported primary_action: {action}")
    zone = str(hand_zone).strip().upper()
    if zone not in HAND_ZONES:
        raise ContractError(f"unsupported hand_zone: {zone}")
    state = normalize_system_state(system.get("state"))
    alert_class = alert_class_for(state, system.get("alert_class"))
    classification = confusion_class(
        system_state=state,
        alert_class=alert_class,
        review_level_value=level,
    )
    clip = normalize_clip(queue_item.get("clip") if isinstance(queue_item.get("clip"), Mapping) else None)
    submitted = submitted_at or utc_now()
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "record_id": safe_identifier(record_id, field="record_id") if record_id else new_id("review"),
        "event_id": event_id,
        "submitted_at_utc": iso_utc(submitted),
        "station_id": station_id,
        "sample_origin": str(queue_item.get("sample_origin", "SYSTEM_ALERT")),
        "system": {
            "state": state,
            "alert_class": alert_class,
            "risk_score": bounded_score(system.get("risk_score", 0)),
            "reason_code": str(system.get("reason_code", ""))[:400],
            "threshold_version": str(system.get("threshold_version", "legacy-v1")),
            "features": deepcopy(system.get("features", {})) if isinstance(system.get("features"), Mapping) else {},
        },
        "human_review": {
            "review_level": level,
            "primary_action": action,
            "hand_zone": zone,
            "material_occluded": bool(material_occluded),
            "frame_annotations": _normalize_frame_annotations(frame_annotations),
            "notes": str(notes)[:1000],
        },
        "derived": {
            "human_positive": level >= 3,
            "confusion_class": classification,
            "buzzer_action": "ALERT" if level >= 3 and queue_item.get("sample_origin") != "SIMULATION" else "SILENT",
        },
        "clip": clip,
        "advisory_only": True,
        "contains_identity": False,
        "machine_control_enabled": False,
    }


def build_actuator_command(
    review_record: Mapping[str, Any],
    *,
    generated_at: datetime | None = None,
    active_seconds: float = 2.2,
    command_id: str | None = None,
) -> dict[str, Any]:
    """Build the only contract permitted to drive either buzzer.

    This function deliberately accepts a completed review record, never a raw
    vision snapshot or queue item.
    """
    if not isinstance(review_record, Mapping) or review_record.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ContractError("a submitted review record is required for actuator output")
    human = review_record.get("human_review")
    if not isinstance(human, Mapping):
        raise ContractError("review record is missing human_review")
    level = review_level(human.get("review_level"))
    if not math.isfinite(active_seconds) or active_seconds <= 0 or active_seconds > 30:
        raise ContractError("active_seconds must be between 0 and 30")
    now = generated_at or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    alert = (
        level >= 3
        and review_record.get("sample_origin") != "SIMULATION"
        and (review_record.get("derived") or {}).get("actuation_inhibited") is not True
    )
    return {
        "schema_version": ACTUATOR_SCHEMA_VERSION,
        "command_id": safe_identifier(command_id, field="command_id") if command_id else new_id("cmd"),
        "event_id": safe_identifier(review_record.get("event_id"), field="event_id"),
        "decision_id": safe_identifier(review_record.get("record_id"), field="record_id"),
        "generated_at_utc": iso_utc(now),
        "expires_at_utc": iso_utc(now + timedelta(seconds=active_seconds)),
        "gate": "HUMAN_REVIEW",
        "review_level": level,
        "action": "ALERT" if alert else "SILENT",
        "buzzer_level": 3 if alert else 0,
        "pattern_id": TWO_SHORT_ONE_LONG if alert else "SILENT",
        "repeat_count": 1 if alert else 0,
        "reason": "human_review_authorized" if alert else "test_mode_or_low_level_output_disabled",
        "risk_score": bounded_score(
            (review_record.get("system") or {}).get("risk_score", 0),
        ),
        "machine_control_enabled": False,
    }


def build_silent_command(
    *, reason: str, generated_at: datetime | None = None, command_id: str | None = None
) -> dict[str, Any]:
    now = generated_at or utc_now()
    return {
        "schema_version": ACTUATOR_SCHEMA_VERSION,
        "command_id": safe_identifier(command_id, field="command_id") if command_id else new_id("cmd"),
        "event_id": None,
        "decision_id": None,
        "generated_at_utc": iso_utc(now),
        "expires_at_utc": iso_utc(now + timedelta(seconds=1.5)),
        "gate": "HUMAN_REVIEW",
        "review_level": None,
        "action": "SILENT",
        "buzzer_level": 0,
        "pattern_id": "SILENT",
        "repeat_count": 0,
        "reason": str(reason)[:240],
        "risk_score": 0,
        "machine_control_enabled": False,
    }


def actuator_is_authorized(payload: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """Return true only for a fresh, internally consistent reviewed alert."""
    try:
        if not isinstance(payload, Mapping):
            return False
        if payload.get("schema_version") != ACTUATOR_SCHEMA_VERSION:
            return False
        if payload.get("gate") != "HUMAN_REVIEW":
            return False
        if payload.get("machine_control_enabled") is not False:
            return False
        if payload.get("action") != "ALERT":
            return False
        if review_level(payload.get("review_level")) < 3:
            return False
        if payload.get("buzzer_level") != 3 or payload.get("pattern_id") != TWO_SHORT_ONE_LONG:
            return False
        if payload.get("repeat_count") != 1:
            return False
        safe_identifier(payload.get("command_id"), field="command_id")
        safe_identifier(payload.get("event_id"), field="event_id")
        safe_identifier(payload.get("decision_id"), field="decision_id")
        generated = parse_utc(payload.get("generated_at_utc"), field="generated_at_utc")
        expires = parse_utc(payload.get("expires_at_utc"), field="expires_at_utc")
        moment = now or utc_now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        return generated <= moment <= expires and expires > generated
    except (ContractError, TypeError, ValueError):
        return False
