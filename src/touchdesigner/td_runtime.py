"""Runtime functions invoked by the TouchDesigner Execute DAT.

This module deliberately keeps computer-vision dependencies outside of
TouchDesigner.  The verified local Python runtime owns screen capture,
MediaPipe inference, privacy blur and the Arduino advisory bridge.  This file
only starts/stops that local sidecar and reloads its anonymous status/dashboard
outputs in TouchDesigner.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ENGINE_LAUNCHER = PROJECT_ROOT / "start_touchdesigner_engine.command"
STATUS_PATH = PROJECT_ROOT / "artifacts" / "ezviz-window-captures" / "live-status.json"
DASHBOARD_PATH = RUNTIME_DIR / "touchdesigner-live-dashboard.jpg"
SOURCE_PREVIEW_PATH = RUNTIME_DIR / "touchdesigner-source-overview.jpg"
STATION_INPUT_PATH = RUNTIME_DIR / "touchdesigner-left-station-input.jpg"
VISION_RESULT_PATH = RUNTIME_DIR / "touchdesigner-opencv-result.jpg"
RISK_CARD_PATH = RUNTIME_DIR / "touchdesigner-risk-decision.jpg"
COMPUTER_BUZZER_PATH = RUNTIME_DIR / "computer-buzzer-simulator.json"
REVIEW_STATE_PATH = RUNTIME_DIR / "safety-officer-review-state.json"
REVIEW_COMMAND_PATH = RUNTIME_DIR / "safety-officer-review-command.json"
REVIEW_ACTUATOR_PATH = RUNTIME_DIR / "review-actuator.json"
NETWORK_PATH = "/safety_td"
WINDOW_PATH = "/safety_window"
# Video should feel live even though pose inference and risk state remain
# conservative.  Status text needs fewer updates than pixels, so keep the two
# schedules independent.
VIDEO_REFRESH_INTERVAL_S = 0.08
STATUS_REFRESH_INTERVAL_S = 0.25
REVIEW_COMMAND_TIMEOUT_S = 5.0
MAX_QUEUED_REVIEW_COMMANDS = 32
_ENGINE_PROCESS = None


STATE_TITLES = {
    "ACTIVE": "正常工作模式",
    "OBSERVING": "正在建立个人基线",
    "UNCERTAIN": "画面不确定（静音）",
    "FATIGUE_TREND": "疲劳趋势提醒",
    "HIGH_RISK": "高风险提醒",
    "FAULT": "系统需要检查",
}


def _network():
    return op(NETWORK_PATH)


def _show_workflow() -> None:
    """Open the visible safety workflow rather than TD's template network."""
    try:
        workflow = _network()
        for name in ("source_overview", "live_dashboard", "opencv_fatigue_result", "risk_dashboard", "operator_hub", "dashboard_display"):
            node = workflow.op(name)
            if node is not None:
                node.viewer = True
        # In recent TouchDesigner builds the pane type's string rendering is
        # not stable.  The current pane is the editor at launch, so address it
        # directly; fall back through the remaining panes if a custom layout
        # has another pane selected.
        panes = [ui.panes.current] + [pane for pane in ui.panes if pane is not ui.panes.current]
        for pane in panes:
            try:
                pane.owner = workflow
                pane.showBackdropTOPs = True
                pane.home()
                return
            except Exception:
                continue
    except Exception:
        # UI focusing is cosmetic; it must never interrupt the safety engine.
        return


def _configure_visible_node_data() -> None:
    """Build compact, readable status cards without giant DAT viewer text."""
    network = _network()
    if network is None:
        return
    # Keep inspectable source/runtime DATs compact.  Their large native viewer
    # font made the page noisy and also truncated the useful information.
    compact_layout = {
        "SYSTEM_LOGIC": (-1320, 560),
        "README": (-1170, 560),
        "python_vision_runtime": (-1020, 560),
        "capture_source": (-870, 560),
        "vision_engine": (-720, 560),
        "waiting_screen": (-550, 545),
        "lifecycle": (-360, 560),
        "td_controller": (-210, 560),
        "safety_officer_review_runtime": (-60, 560),
        "risk_dashboard_to_arduino": (250, -420),
        "operator_note": (410, -420),
        "arduino_alert": (570, -420),
        "live_status": (730, -420),
        "risk_event": (890, -420),
        "arduino_buzzer_trigger": (1050, -420),
        "computer_buzzer_simulator": (1210, -420),
        "computer_buzzer_note": (1370, -420),
        "computer_buzzer_simulator_code": (1530, -420),
    }
    for name, (x, y) in compact_layout.items():
        node = network.op(name)
        if node is None:
            continue
        try:
            node.nodeX = x
            node.nodeY = y
            node.nodeWidth = 130
            node.nodeHeight = 75
            node.viewer = False
        except Exception:
            pass

    # All image thumbnails show the complete texture.  ``outside`` crops the
    # image and was the reason labels/data at the left and right edges appeared
    # missing in earlier project versions.
    for name in (
        "source_overview",
        "raw_video_to_opencv",
        "live_dashboard",
        "opencv_fatigue_result",
        "dashboard_switch",
        "risk_dashboard",
        "dashboard_display",
    ):
        node = network.op(name)
        if node is None:
            continue
        _set_value(node, "fillmode", "best")
        _set_value(node, "filtertype", "linear")
        try:
            node.viewer = True
        except Exception:
            pass

    # Keep the human-review gate visually separate from the raw-risk and
    # actuator cards.  The node's 16:9 thumbnail is the selected evidence clip.
    hub = network.op("operator_hub")
    if hub is not None:
        try:
            hub.nodeX = 620
            hub.nodeY = -720
            hub.nodeWidth = 520
            hub.nodeHeight = 292
            hub.viewer = True
            hub.activeViewer = True
            review_panel = hub.op("safety_officer_review")
            if review_panel is not None:
                review_panel.activeViewer = True
        except Exception:
            pass

    cards = {
        "system_live_card": (-1320, 300),
        "capture_live_card": (-920, 300),
        "vision_live_card": (-520, 300),
        "risk_live_card": (250, -235),
        "arduino_live_card": (650, -235),
        "buzzer_live_card": (1050, -235),
        "review_live_card": (40, -545),
    }
    for name, (x, y) in cards.items():
        card = network.op(name)
        if card is None:
            try:
                card = network.create(textTOP, name)
            except Exception:
                continue
        card.nodeX = x
        card.nodeY = y
        card.nodeWidth = 520 if name == "review_live_card" else 380
        card.nodeHeight = 164
        card.viewer = True
        # Text TOP defaults to a square output.  A wide node viewer then shows
        # black side bands and clips the centre text.  Give every card a real
        # wide output so the viewer and its pixels have the same aspect ratio.
        _set_value(card, "outputresolution", "custom")
        _set_value(card, "resolutionw", 1040 if name == "review_live_card" else 760)
        _set_value(card, "resolutionh", 328)
        _set_value(card, "outputaspect", "resolution")
        _set_value(card, "fillmode", "best")
        _set_value(card, "filtertype", "linear")
        _set_value(card, "fontsize", 18)
        _set_value(card, "wordwrap", False)
        _set_value(card, "alignx", "left")
        _set_value(card, "aligny", "top")
        _set_value(card, "fontcolorr", 0.82)
        _set_value(card, "fontcolorg", 0.86)
        _set_value(card, "fontcolorb", 0.90)
        _set_value(card, "bgcolorr", 0.045)
        _set_value(card, "bgcolorg", 0.052)
        _set_value(card, "bgcolorb", 0.065)
        _set_value(card, "bgalpha", 1.0)
        _set_value(card, "text", "正在读取实时状态…")


def _configure_review_panel_layout() -> None:
    """Repair review-control spacing in both old and newly built .toe files.

    The first UI revision placed the hand-zone row across the annotation
    heading.  Apply geometry at startup so the formal desktop launcher is
    corrected without rebuilding, deleting, or disconnecting any operators.
    """
    _hub, review = _review_nodes()
    if review is None:
        return
    layout = {
        "ui_action_header": (548, 326, 708, 30),
        "ui_grade_header": (548, 166, 708, 30),
        "action_normal_pick": (560, 270, 150, 44),
        "action_waste_cleanup": (726, 270, 150, 44),
        "action_violation": (892, 270, 150, 44),
        "action_style_change": (1058, 270, 150, 44),
        "hand_zone_safe": (560, 214, 150, 44),
        "hand_zone_attention": (726, 214, 150, 44),
        "hand_zone_danger": (892, 214, 150, 44),
        "hand_zone_unknown": (1058, 214, 150, 44),
        "danger_level_1": (560, 102, 122, 48),
        "danger_level_2": (694, 102, 122, 48),
        "danger_level_3": (828, 102, 122, 48),
        "danger_level_4": (962, 102, 122, 48),
        "danger_level_5": (1096, 102, 122, 48),
    }
    for name, (x, y, width, height) in layout.items():
        node = _review_control(review, name)
        if node is None:
            continue
        _set_value(node, "x", x)
        _set_value(node, "y", y)
        _set_value(node, "w", width)
        _set_value(node, "h", height)


def _set_card(name: str, lines: list[str]) -> None:
    card = _network().op(name)
    if card is not None:
        _set_value(card, "text", "\n".join(lines))


def _reason_label(reason: str) -> str:
    """Turn internal state codes into one complete, readable card label."""
    if "video_content_unchanged" in reason:
        return "画面长时间无变化，请确认直播未暂停（非安全结论）"
    token = reason.rsplit(":", 1)[-1]
    labels = {
        "waiting_for_EZVIZ_window": "等待萤石窗口画面",
        "window_capture_unavailable": "萤石窗口暂不可用",
        "no_reliable_whole_person_pose": "未检测到可靠全身姿态",
        "temporary_pose_gap": "全身姿态短暂丢失",
        "building_personal_work_baseline": "正在建立个人工作基线",
        "multiple_posture_changes_persisted": "多项姿态变化持续存在",
        "sustained_posture_and_activity_change": "姿态与活动持续变化",
        "work_pattern_within_personal_baseline": "工作状态处于个人基线",
    }
    if token in labels:
        return labels[token]
    return token.replace("_", " ")[:28] or "等待数据"


def _set_text(name: str, text: str) -> None:
    node = _network().op(name)
    if node is not None:
        node.text = text


def _parameter(node, name: str):
    try:
        return getattr(node.par, name)
    except AttributeError:
        return None


def _pulse(node, *names: str) -> None:
    for name in names:
        parameter = _parameter(node, name)
        if parameter is not None:
            try:
                parameter.pulse()
                return
            except Exception:
                pass


def _set_value(node, name: str, value) -> None:
    parameter = _parameter(node, name)
    if parameter is not None:
        try:
            parameter.val = value
        except Exception:
            pass


def _read_status() -> tuple[dict, str]:
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid status payload")
        age_s = max(0.0, time.time() - STATUS_PATH.stat().st_mtime)
        payload["_age_s"] = age_s
        return payload, "ok"
    except FileNotFoundError:
        return {"state": "OBSERVING", "score": 0, "reason": "waiting_for_EZVIZ_window"}, "waiting"
    except Exception as error:
        return {"state": "FAULT", "score": 0, "reason": f"status_read_error:{type(error).__name__}"}, "error"


def _read_computer_buzzer() -> dict:
    """Read the no-hardware audio simulator status for the visible workflow."""
    try:
        payload = json.loads(COMPUTER_BUZZER_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_review_state() -> dict:
    try:
        payload = json.loads(REVIEW_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        if not 0 <= time.time() - REVIEW_STATE_PATH.stat().st_mtime <= 5.0:
            payload["service_state"] = "STALE"
            payload["actuator"] = {"action": "SILENT"}
            payload["last_result"] = {"ok": False, "message": "审核服务未更新，请等待恢复；旧状态不是运行证明"}
        supervisor_path = RUNTIME_DIR / "review-supervisor.json"
        if supervisor_path.is_file() and 0 <= time.time() - supervisor_path.stat().st_mtime < 3.0:
            health = json.loads(supervisor_path.read_text(encoding="utf-8"))
            if health.get("state") in {"BACKOFF", "FAULT_RETRY_LIMIT", "STOPPED"}:
                payload["service_state"] = health["state"]
                payload["last_result"] = {"ok": False, "message": "审核服务正在恢复" if health["state"] == "BACKOFF" else "审核服务已停止；请检查日志后重启项目"}
        return payload
    except Exception:
        return {}


def _new_review_command(action: str, **values) -> dict:
    """Create one immutable, idempotent command payload."""
    sequence = int(_network().fetch("review_command_sequence", 0)) + 1
    _network().store("review_command_sequence", sequence)
    payload = {
        "schema_version": 1,
        "command_id": f"td-{int(time.time() * 1000)}-{sequence}",
        "created_at_epoch_s": round(time.time(), 3),
        "action": str(action).upper(),
        "machine_control_enabled": False,
    }
    payload.update(values)
    return payload


def _write_command_file(payload: dict) -> None:
    """Atomically publish exactly one command for the review sidecar."""
    temporary = REVIEW_COMMAND_PATH.with_suffix(".json.tmp")
    REVIEW_COMMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(REVIEW_COMMAND_PATH)


def _flush_review_command_queue(state: dict | None = None) -> None:
    """Deliver queued UI actions one at a time and wait for acknowledgement.

    A single JSON command file is intentionally used as a narrow process
    boundary.  Without this queue, two buttons handled during the same TD tick
    could overwrite each other before the sidecar polls the file.
    """
    network = _network()
    if network is None:
        return
    queued = list(network.fetch("review_command_queue", []) or [])
    active_id = str(network.fetch("review_active_command_id", "") or "")
    active_since = float(network.fetch("review_active_command_since", 0.0) or 0.0)
    state = state if isinstance(state, dict) else _read_review_state()
    acknowledged = str(state.get("last_command_id", "") or "")

    if active_id and acknowledged == active_id:
        queued = [item for item in queued if str(item.get("command_id", "")) != active_id]
        active_id = ""
        network.store("review_active_command_id", "")
        network.store("review_active_command_since", 0.0)
        network.store("review_command_queue", queued)
    elif active_id:
        if time.monotonic() - active_since > REVIEW_COMMAND_TIMEOUT_S:
            _set_text("operator_note", "安全员审核服务未确认上一条命令；为防止丢失/重复，后续操作已暂停发送。")
        return

    if not queued:
        return
    payload = queued[0]
    try:
        _write_command_file(payload)
    except OSError as error:
        _set_text("operator_note", f"审核命令写入失败：{type(error).__name__}")
        return
    network.store("review_active_command_id", str(payload.get("command_id", "")))
    network.store("review_active_command_since", time.monotonic())


def _write_review_command(action: str, **values) -> None:
    """Queue one command; never overwrite an unacknowledged operator action."""
    network = _network()
    queued = list(network.fetch("review_command_queue", []) or [])
    if len(queued) >= MAX_QUEUED_REVIEW_COMMANDS:
        _set_text("operator_note", "审核操作队列已满，请等待服务确认当前操作。")
        return
    queued.append(_new_review_command(action, **values))
    network.store("review_command_queue", queued)
    _flush_review_command_queue()


def _replace_table(node, rows) -> None:
    if node is None:
        return
    try:
        node.clear()
        for row in rows:
            node.appendRow(tuple("" if value is None else str(value) for value in row))
    except Exception:
        pass


def _review_nodes():
    hub = _network().op("operator_hub")
    review = hub.op("safety_officer_review") if hub is not None else None
    return hub, review


def _button_state(button) -> bool:
    if button is None:
        return False
    value = _parameter(button, "value0")
    if value is not None:
        try:
            return bool(value.eval())
        except Exception:
            pass
    try:
        return bool(button.panel.state)
    except Exception:
        return False


def _set_button_state(button, value: bool, *, remember: bool = True) -> None:
    if button is None:
        return
    _set_value(button, "value0", bool(value))
    if remember:
        _network().store(f"review_button_{button.name}", bool(value))


def _button_changed(button) -> bool:
    if button is None:
        return False
    key = f"review_button_{button.name}"
    current = _button_state(button)
    previous = _network().fetch(key, None)
    _network().store(key, current)
    return previous is not None and current != bool(previous)


def _button_rising(button) -> bool:
    return _button_changed(button) and _button_state(button)


def _consume_button(button) -> bool:
    """Consume a latched command button exactly once."""
    if not _button_rising(button):
        return False
    _set_button_state(button, False)
    return True


def _set_button_label(button, label: str) -> None:
    if button is None:
        return
    _set_value(button, "label", label)
    _set_value(button, "text", label)


def _parse_utc(value) -> datetime | None:
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _review_actuator_is_authorized(actuator: dict, review_state: dict) -> bool:
    """Fail closed unless the complete human-review contract is valid/fresh."""
    if not isinstance(actuator, dict) or actuator.get("schema_version") != 1:
        return False
    if actuator.get("gate") != "HUMAN_REVIEW" or actuator.get("action") != "ALERT":
        return False
    if actuator.get("machine_control_enabled") is not False:
        return False
    level = actuator.get("review_level")
    if isinstance(level, bool) or not isinstance(level, int) or not 3 <= level <= 5:
        return False
    if actuator.get("buzzer_level") != 3:
        return False
    if actuator.get("pattern_id") != "TWO_SHORT_ONE_LONG" or actuator.get("repeat_count") != 1:
        return False
    if not all(str(actuator.get(key, "")).strip() for key in ("command_id", "event_id", "decision_id")):
        return False
    generated = _parse_utc(actuator.get("generated_at_utc"))
    expires = _parse_utc(actuator.get("expires_at_utc"))
    now = datetime.now(UTC)
    if generated is None or expires is None or not (generated <= now <= expires and expires > generated):
        return False
    input_mode = review_state.get("input_mode") if isinstance(review_state.get("input_mode"), dict) else {}
    if str(input_mode.get("mode", "LIVE")).upper() != "LIVE":
        return False
    if input_mode.get("physical_actuator_allowed") is not True:
        return False
    return str(review_state.get("service_state", "")).upper() == "RUNNING"


def _all_review_buttons(review) -> list:
    names = [
        *(f"danger_level_{grade}" for grade in range(1, 6)),
        "action_normal_pick",
        "action_waste_cleanup",
        "action_violation",
        "action_style_change",
        "hand_zone_safe",
        "hand_zone_attention",
        "hand_zone_danger",
        "hand_zone_unknown",
        "material_occluded",
        "previous_alert",
        "next_alert",
        "previous_frame",
        "play_pause",
        "next_frame",
        "submit_review",
        "manual_review_capture",
        "acknowledge_and_silence",
        "generate_threshold_advice",
        "apply_approved_thresholds",
        "test_mode_switch",
        "run_simulation_test",
    ]
    return [button for name in names if (button := _review_control(review, name)) is not None]


def _review_control(review, name: str):
    """Return the visible Panel COMP from both legacy and rebuilt projects.

    Some historical .toe files already contained documentation operators with
    the intended names.  TouchDesigner therefore added ``1`` to the actual
    Button COMPs.  Prefer that visible control when present, while keeping the
    clean unsuffixed names used by current builds.
    """
    if review is None:
        return None
    suffixed = review.op(f"{name}1")
    return suffixed if suffixed is not None else review.op(name)


def _initialise_review_controls(review) -> None:
    """Prime edge storage so loading a saved .toe cannot replay old clicks."""
    if review is None:
        return
    command_names = {
        "previous_alert",
        "next_alert",
        "previous_frame",
        "next_frame",
        "submit_review",
        "manual_review_capture",
        "acknowledge_and_silence",
        "generate_threshold_advice",
        "apply_approved_thresholds",
        "run_simulation_test",
    }
    command_paths = {
        button.path
        for name in command_names
        if (button := _review_control(review, name)) is not None
    }
    for button in _all_review_buttons(review):
        if button.path in command_paths:
            _set_button_state(button, False)
        else:
            _network().store(f"review_button_{button.name}", _button_state(button))


def _reset_review_form_for_event(review, event_id) -> None:
    network = _network()
    event_key = str(event_id or "")
    if str(network.fetch("review_form_event_id", "")) == event_key:
        return
    network.store("review_form_event_id", event_key)
    network.store("review_level", None)
    network.store("review_action", None)
    network.store("review_hand_zone", "NOT_VISIBLE")
    network.store("review_material_occluded", False)
    for grade in range(1, 6):
        _set_button_state(_review_control(review, f"danger_level_{grade}"), False)
    for name in (
        "action_normal_pick",
        "action_waste_cleanup",
        "action_violation",
        "action_style_change",
        "hand_zone_safe",
        "hand_zone_attention",
        "hand_zone_danger",
    ):
        _set_button_state(_review_control(review, name), False)
    _set_button_state(_review_control(review, "hand_zone_unknown"), True)
    _set_button_state(_review_control(review, "material_occluded"), False)
    _set_button_state(_review_control(review, "play_pause"), False)


def _move_review_selection(state: dict, delta: int) -> None:
    rows = state.get("queue") if isinstance(state.get("queue"), list) else []
    reviewable = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("queue_status") in {"WAITING_CLIP", "PENDING_REVIEW", "IN_REVIEW"}
        and row.get("event_id")
    ]
    if not reviewable:
        _set_text("operator_note", "当前没有待审核预警。")
        return
    ids = [str(row["event_id"]) for row in reviewable]
    current = str(state.get("selected_event_id") or "")
    try:
        index = ids.index(current)
    except ValueError:
        index = 0 if delta >= 0 else len(ids) - 1
    else:
        index = (index + delta) % len(ids)
    _write_review_command("SELECT_EVENT", event_id=ids[index])


def _simulation_video_path(review) -> Path | None:
    source = review.op("simulation_video_input") if review is not None else None
    parameter = _parameter(source, "file")
    try:
        value = str(parameter.eval()).strip() if parameter is not None else ""
    except Exception:
        value = ""
    if not value:
        return None
    try:
        path = Path(value).expanduser().resolve()
    except Exception:
        return None
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".avi"}:
        return None
    allowed = (PROJECT_ROOT / "test_videos").resolve()
    return path if allowed in path.parents else None


def _step_review_clip(clip, delta: int) -> None:
    """Move exactly one source-movie frame using Movie File In's index mode."""
    if clip is None:
        return
    try:
        current = float(clip.index)
    except Exception:
        parameter = _parameter(clip, "index")
        try:
            current = float(parameter.eval()) if parameter is not None else 0.0
        except Exception:
            current = 0.0
    try:
        upper = max(0.0, float(clip.numImages) - 1.0)
    except Exception:
        upper = max(0.0, current + 1.0)
    _set_value(clip, "play", False)
    _set_value(clip, "playmode", "specify")
    _set_value(clip, "indexunit", "indices")
    _set_value(clip, "index", max(0.0, min(upper, current + int(delta))))


def _selected_review_values() -> tuple[int | None, str | None, str, bool]:
    network = _network()
    level = network.fetch("review_level", None)
    action = network.fetch("review_action", None)
    hand_zone = str(network.fetch("review_hand_zone", "NOT_VISIBLE"))
    occluded = bool(network.fetch("review_material_occluded", False))
    try:
        level = int(level) if level is not None else None
    except (TypeError, ValueError):
        level = None
    return level, str(action) if action else None, hand_zone, occluded


def _handle_review_controls(review, state: dict) -> None:
    if review is None:
        return
    grade_nodes = {grade: _review_control(review, f"danger_level_{grade}") for grade in range(1, 6)}
    for grade, button in grade_nodes.items():
        if _button_rising(button):
            _network().store("review_level", grade)
    action_nodes = {
        "NORMAL_PICK": _review_control(review, "action_normal_pick"),
        "WASTE_CLEANUP": _review_control(review, "action_waste_cleanup"),
        "VIOLATION": _review_control(review, "action_violation"),
        "STYLE_CHANGE": _review_control(review, "action_style_change"),
    }
    for value, button in action_nodes.items():
        if _button_rising(button):
            _network().store("review_action", value)
    zone_nodes = {
        "SAFE": _review_control(review, "hand_zone_safe"),
        "CAUTION": _review_control(review, "hand_zone_attention"),
        "DANGER": _review_control(review, "hand_zone_danger"),
        "NOT_VISIBLE": _review_control(review, "hand_zone_unknown"),
    }
    for value, button in zone_nodes.items():
        if _button_rising(button):
            _network().store("review_hand_zone", value)
    occlusion = _review_control(review, "material_occluded")
    occluded = _button_state(occlusion)
    _network().store("review_material_occluded", occluded)

    clip = review.op("review_clip_playback")
    play_button = _review_control(review, "play_pause")
    if _button_changed(play_button) and clip is not None:
        playing = _button_state(play_button)
        if playing:
            try:
                current_index = float(clip.index)
            except Exception:
                current_index = 0.0
            _set_value(clip, "playmode", "sequential")
            _set_value(clip, "cuepointunit", "indices")
            _set_value(clip, "cuepoint", current_index)
            _pulse(clip, "cuepulse", "cue")
        _set_value(clip, "play", playing)
        _set_button_label(play_button, "Ⅱ 暂停" if playing else "▶ 播放")
    if _consume_button(_review_control(review, "previous_frame")) and clip is not None:
        _set_value(clip, "play", False)
        _set_button_state(play_button, False)
        _set_button_label(play_button, "▶ 播放")
        _step_review_clip(clip, -1)
    if _consume_button(_review_control(review, "next_frame")) and clip is not None:
        _set_value(clip, "play", False)
        _set_button_state(play_button, False)
        _set_button_label(play_button, "▶ 播放")
        _step_review_clip(clip, 1)

    selected = state.get("selected") if isinstance(state.get("selected"), dict) else {}
    event_id = state.get("selected_event_id") or selected.get("event_id")
    if _consume_button(_review_control(review, "previous_alert")):
        _move_review_selection(state, -1)
    if _consume_button(_review_control(review, "next_alert")):
        _move_review_selection(state, 1)
    if _consume_button(_review_control(review, "submit_review")):
        level, action, zone, hidden = _selected_review_values()
        input_mode = state.get("input_mode") if isinstance(state.get("input_mode"), dict) else {}
        is_test_mode = str(input_mode.get("mode", "LIVE")).upper() == "TEST"
        if not event_id:
            _set_text("operator_note", "当前没有可提交的待审核事件。")
        elif level is None or action is None:
            _set_text("operator_note", "提交前必须选择：危险等级 1–5 + 动作类型。")
        elif is_test_mode:
            # Until the sidecar acknowledges a dedicated non-actuating test
            # submission contract, never send a production SUBMIT_REVIEW from
            # TEST mode.  Record the observation locally and stay fail-closed.
            video = _simulation_video_path(review)
            log = review.op("simulation_test_log")
            try:
                log.appendRow(
                    (
                        time.strftime("%H:%M:%S"),
                        video.name if video else "-",
                        level,
                        selected.get("system_state", "-"),
                        selected.get("peak_score", selected.get("risk_score", "-")),
                        "待对照",
                        f"{action}/{zone}; TEST 模式未写入实体执行器",
                    )
                )
            except Exception:
                pass
            _set_text("operator_note", "模拟测试结果已记录；TEST 模式不向实体或电脑蜂鸣器发送命令。")
        else:
            _write_review_command(
                "SUBMIT_REVIEW",
                event_id=event_id,
                review_level=level,
                primary_action=action,
                hand_zone=zone,
                material_occluded=hidden,
                notes="TouchDesigner safety officer review",
            )
    if _consume_button(_review_control(review, "acknowledge_and_silence")):
        if event_id:
            _write_review_command("ACKNOWLEDGE", event_id=event_id)
        else:
            _set_text("operator_note", "当前没有可确认的事件。")
    if _consume_button(_review_control(review, "manual_review_capture")):
        _write_review_command("MANUAL_MISSED")
    if _consume_button(_review_control(review, "generate_threshold_advice")):
        _write_review_command("PROPOSE_THRESHOLDS")
    if _consume_button(_review_control(review, "apply_approved_thresholds")):
        proposal = state.get("threshold_proposal") if isinstance(state.get("threshold_proposal"), dict) else {}
        if proposal.get("status") == "PROPOSED" and proposal.get("requires_explicit_approval") is True:
            _write_review_command("APPLY_THRESHOLDS", explicit_approval=True)
        else:
            _set_text("operator_note", "阈值建议尚未达到 PROPOSED 状态，不允许应用。")
    test_button = _review_control(review, "test_mode_switch")
    if _button_changed(test_button):
        mode = "TEST" if _button_state(test_button) else "LIVE"
        video = _simulation_video_path(review) if mode == "TEST" else None
        _network().store("review_desired_input_mode", mode)
        _network().store("review_desired_input_mode_at", time.monotonic())
        _write_review_command("SET_INPUT_MODE", mode=mode, test_video=str(video) if video else None)
    if _consume_button(_review_control(review, "run_simulation_test")):
        video = _simulation_video_path(review)
        if video is None:
            _set_text("operator_note", "请先在 simulation_video_input 选择项目 test_videos 文件夹中的视频。")
        else:
            _set_button_state(test_button, True)
            _network().store("review_desired_input_mode", "TEST")
            _network().store("review_desired_input_mode_at", time.monotonic())
            _set_value(review.op("simulation_video_input"), "play", True)
            _write_review_command("SET_INPUT_MODE", mode="TEST", test_video=str(video))
            log = review.op("simulation_test_log")
            try:
                log.appendRow((time.strftime("%H:%M:%S"), video.name, "待设定", "REQUESTED", "-", "-", "测试模式禁止实体蜂鸣器"))
            except Exception:
                pass


def _update_review_ui() -> dict:
    """Populate every visible review operator from one sidecar state file."""
    state = _read_review_state()
    _flush_review_command_queue(state)
    hub, review = _review_nodes()
    if hub is None or review is None:
        return state
    rows = state.get("queue") if isinstance(state.get("queue"), list) else []
    selected_event_id = str(state.get("selected_event_id") or "")
    header = ("选中", "event", "time", "station", "system_state", "score", "status", "clip")
    table_rows = [header]
    for row in reversed(rows[-20:]):
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or "")
        table_rows.append(
            (
                "▶" if event_id == selected_event_id else "",
                event_id[-14:] if event_id else "-",
                str(row.get("time", ""))[11:19],
                row.get("station"),
                row.get("system_state"),
                row.get("peak_score", row.get("risk_score")),
                row.get("queue_status"),
                row.get("clip_status"),
            )
        )
    _replace_table(review.op("pending_alert_queue"), table_rows)
    _replace_table(hub.op("pending_alert_index"), table_rows[:7])

    selected = state.get("selected") if isinstance(state.get("selected"), dict) else {}
    _reset_review_form_for_event(review, selected_event_id)
    clip_path = selected.get("clip_path")
    clip = review.op("review_clip_playback")
    clip_display = review.op("review_clip_display")
    clip_ready = bool(clip_path and Path(str(clip_path)).is_file())
    if clip is not None and clip_ready:
        prior = str(_network().fetch("review_clip_path", ""))
        if prior != str(clip_path):
            _set_value(clip, "file", str(clip_path))
            _pulse(clip, "reload", "reloadpulse")
            _set_value(clip, "playmode", "sequential")
            _set_value(clip, "play", False)
            _set_value(clip, "cuepointunit", "indices")
            _set_value(clip, "cuepoint", 0)
            _pulse(clip, "cuepulse", "cue")
            _set_button_state(_review_control(review, "play_pause"), False)
            _set_button_label(_review_control(review, "play_pause"), "▶ 播放")
            _network().store("review_clip_path", str(clip_path))
    elif clip is not None:
        _set_value(clip, "play", False)
        _set_button_state(_review_control(review, "play_pause"), False)
        _set_button_label(_review_control(review, "play_pause"), "▶ 播放")
        _network().store("review_clip_path", "")
    _set_value(clip_display, "index", 1 if clip_ready else 0)

    # Read controls before rendering the form so a click is visible during the
    # same status refresh, then let the command queue deliver it losslessly.
    _handle_review_controls(review, state)
    _flush_review_command_queue(state)

    stats = state.get("statistics") if isinstance(state.get("statistics"), dict) else {}
    levels = stats.get("level_distribution") if isinstance(stats.get("level_distribution"), dict) else {}
    accuracy = (
        f"{float(stats.get('accuracy')) * 100:.1f}%"
        if stats.get("accuracy_available") and isinstance(stats.get("accuracy"), (int, float))
        else "N/A（缺少无报警抽检）"
    )
    _replace_table(
        review.op("today_review_stats"),
        (
            ("metric", "value"),
            ("pending_reviews", state.get("pending_count", 0)),
            ("reviewed_today", stats.get("reviewed_count", 0)),
            ("valid_alerts", stats.get("valid_alarm_count", 0)),
            ("false_positives", stats.get("false_alarm_count", 0)),
            ("reviewed_precision", stats.get("reviewed_alert_precision")),
            ("system_accuracy", accuracy),
        ),
    )
    confusion = stats.get("confusion") if isinstance(stats.get("confusion"), dict) else {}
    _replace_table(
        review.op("sample_classification"),
        (
            ("sample_class", "rule", "today_count"),
            ("TRUE_POSITIVE", "人工3–5级 + 系统已报警", confusion.get("TRUE_POSITIVE", 0)),
            ("FALSE_POSITIVE", "人工1–2级 + 系统已报警", confusion.get("FALSE_POSITIVE", 0)),
            ("FALSE_NEGATIVE", "补录人工3–5级 + 系统未报", confusion.get("FALSE_NEGATIVE", 0)),
            ("TRUE_NEGATIVE", "抽检人工1–2级 + 系统未报", confusion.get("TRUE_NEGATIVE", 0)),
            ("ABSTAIN", "UNCERTAIN 仅供现场确认", int(confusion.get("ABSTAIN_POSITIVE", 0)) + int(confusion.get("ABSTAIN_NEGATIVE", 0))),
        ),
    )
    reviewed_rows = [
        row for row in reversed(rows)
        if isinstance(row, dict) and row.get("review_level") is not None
    ][:12]
    _replace_table(
        review.op("annotation_records"),
        [
            ("time", "event", "station", "system_state", "score", "review_level", "status", "clip"),
            *[
                (
                    str(row.get("time", ""))[11:19],
                    str(row.get("event_id", ""))[-14:],
                    row.get("station"),
                    row.get("system_state"),
                    row.get("peak_score", row.get("risk_score")),
                    row.get("review_level"),
                    row.get("queue_status"),
                    row.get("clip_status"),
                )
                for row in reviewed_rows
            ],
        ],
    )
    _set_value(
        review.op("review_statistics_panel"),
        "text",
        "\n".join(
            [
                "TODAY / 今日审核",
                f"待审核 {state.get('pending_count', 0)}   已审核 {stats.get('reviewed_count', 0)}",
                f"有效报警 {stats.get('valid_alarm_count', 0)}   误报 {stats.get('false_alarm_count', 0)}",
                "1–5级分布 " + " / ".join(str(levels.get(str(index), 0)) for index in range(1, 6)),
                f"系统准确率 {accuracy}",
                "只有加入无报警抽检/漏报补录后才计算准确率",
            ]
        ),
    )
    actuator = state.get("actuator") if isinstance(state.get("actuator"), dict) else {}
    actuator_authorized = _review_actuator_is_authorized(actuator, state)
    actuator_reason = actuator.get("reason")
    if actuator.get("action") == "ALERT" and not actuator_authorized:
        actuator_reason = "review_contract_rejected_or_expired"
    _replace_table(
        hub.op("reviewed_actuator_state"),
        (
            ("review_status", "review_level", "source_state", "source_score", "buzzer_allowed", "pattern", "reason"),
            (
                selected.get("queue_status", "等待审核"),
                actuator.get("review_level"),
                selected.get("system_state", "UNCERTAIN"),
                selected.get("peak_score", 0),
                actuator_authorized,
                actuator.get("pattern_id", "SILENT") if actuator_authorized else "SILENT",
                actuator_reason or "human_review_required",
            ),
        ),
    )
    proposal = state.get("threshold_proposal") if isinstance(state.get("threshold_proposal"), dict) else {}
    suggested = proposal.get("suggested") if isinstance(proposal.get("suggested"), dict) else {}
    current = proposal.get("current") if isinstance(proposal.get("current"), dict) else {}
    _replace_table(
        review.op("threshold_recommendations"),
        (
            ("parameter", "current", "suggested", "status", "auto_apply"),
            ("warning_min", current.get("warning_min", 45), suggested.get("warning_min", "-"), proposal.get("status", "NOT_READY"), False),
            ("danger_min", current.get("danger_min", 70), suggested.get("danger_min", "-"), proposal.get("status", "NOT_READY"), False),
            ("guardrail", "explicit approval", "required", proposal.get("reason", "需要足够标注样本"), False),
        ),
    )
    apply_button = _review_control(review, "apply_approved_thresholds")
    proposal_ready = proposal.get("status") == "PROPOSED" and proposal.get("requires_explicit_approval") is True
    _set_value(apply_button, "enable", proposal_ready)
    _set_button_label(apply_button, "审批后应用" if proposal_ready else "样本不足／禁止应用")
    level, action, zone, hidden = _selected_review_values()
    _replace_table(
        review.op("annotation_form"),
        (
            ("field", "selected_value", "required"),
            ("danger_level", level or "未选择", "yes"),
            ("action_type", action or "未选择", "yes"),
            ("hand_zone", zone, "yes"),
            ("material_occluded", hidden, "yes"),
            ("event_id", selected_event_id[-18:] if selected_event_id else "-", "yes"),
        ),
    )
    input_mode = state.get("input_mode") if isinstance(state.get("input_mode"), dict) else {}
    external_mode = str(input_mode.get("mode", "LIVE")).upper()
    desired_mode = str(_network().fetch("review_desired_input_mode", "") or "").upper()
    desired_at = float(_network().fetch("review_desired_input_mode_at", 0.0) or 0.0)
    test_button = _review_control(review, "test_mode_switch")
    if desired_mode and desired_mode != external_mode and time.monotonic() - desired_at <= REVIEW_COMMAND_TIMEOUT_S:
        _set_button_label(test_button, f"测试模式：切换中→{desired_mode}")
    else:
        if desired_mode:
            _network().store("review_desired_input_mode", "")
            _network().store("review_desired_input_mode_at", 0.0)
        _set_button_state(test_button, external_mode == "TEST")
        _set_button_label(test_button, f"测试模式：{'开' if external_mode == 'TEST' else '关'}")
    test_video = review.op("simulation_video_input")
    _set_value(test_video, "play", external_mode == "TEST" and _simulation_video_path(review) is not None)
    return state


def _title_for(payload: dict, engine_state: str) -> str:
    state = str(payload.get("state", "OBSERVING")).upper()
    score = payload.get("risk_score", payload.get("score", 0))
    title = STATE_TITLES.get(state, state)
    suffix = "引擎运行中" if engine_state == "running" else "等待萤石云视频画面"
    return f"萤石安全视界 TouchDesigner版  |  {title}  |  {score}/100  |  {suffix}"


def _update_dashboard() -> None:
    network = _network()
    source = network.op("source_overview")
    dashboard = network.op("live_dashboard")
    opencv_result = network.op("opencv_fatigue_result")
    risk_card = network.op("risk_dashboard")
    final_display = network.op("dashboard_display")
    switch = network.op("dashboard_switch")
    if dashboard is None or switch is None:
        return
    source_fresh = (
        SOURCE_PREVIEW_PATH.is_file()
        and time.time() - SOURCE_PREVIEW_PATH.stat().st_mtime <= 1.8
    )
    def reload_changed(node, path: Path, *, alternate_path: bool = False) -> None:
        if node is None or not path.is_file():
            return
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            return
        cache_key = f"td_image_mtime_{node.name}"
        if int(network.fetch(cache_key, 0)) == modified:
            return
        load_path = path
        if alternate_path:
            # TouchDesigner can retain the first GPU texture when a JPEG is
            # overwritten at the same path.  Alternating two validated mirror
            # filenames forces a real texture upload and prevents a permanent
            # black video panel after launch.
            try:
                payload = path.read_bytes()
                if path.suffix.lower() in {".jpg", ".jpeg"} and not payload.endswith(b"\xff\xd9"):
                    return
                slot_key = f"td_image_slot_{node.name}"
                slot = 1 - int(network.fetch(slot_key, 0) or 0)
                load_path = RUNTIME_DIR / f"touchdesigner-cache-{node.name}-{slot}{path.suffix.lower()}"
                temporary = load_path.with_suffix(load_path.suffix + ".tmp")
                temporary.write_bytes(payload)
                temporary.replace(load_path)
                network.store(slot_key, slot)
            except OSError:
                return
        _set_value(node, "file", str(load_path))
        _pulse(node, "reload", "reloadpulse")
        network.store(cache_key, modified)

    if source is not None and source_fresh:
        reload_changed(source, SOURCE_PREVIEW_PATH, alternate_path=True)
    for node, path in (
        (dashboard, STATION_INPUT_PATH),
        (opencv_result, VISION_RESULT_PATH),
        (risk_card, RISK_CARD_PATH),
        (final_display, DASHBOARD_PATH),
    ):
        if node is not None and path.is_file() and time.time() - path.stat().st_mtime <= 1.8:
            reload_changed(node, path, alternate_path=True)
    vision_fresh = (
        VISION_RESULT_PATH.is_file()
        and time.time() - VISION_RESULT_PATH.stat().st_mtime <= 1.8
    )
    if vision_fresh:
        _set_value(switch, "index", 1)
    else:
        _set_value(switch, "index", 0)


def _engine_state() -> str:
    process = _ENGINE_PROCESS
    if process is None:
        return "not_started"
    return "running" if process.poll() is None else f"stopped:{process.returncode}"


def _publish_status() -> None:
    payload, read_state = _read_status()
    review_state = _read_review_state()
    actuator = review_state.get("actuator") if isinstance(review_state.get("actuator"), dict) else {}
    pending_reviews = int(review_state.get("pending_count", 0) or 0)
    review_action = str(actuator.get("action", "SILENT")).upper()
    review_level = actuator.get("review_level")
    review_authorized = _review_actuator_is_authorized(actuator, review_state)
    input_mode = review_state.get("input_mode") if isinstance(review_state.get("input_mode"), dict) else {}
    mode_name = str(input_mode.get("mode", "LIVE")).upper()
    state = str(payload.get("state", "OBSERVING")).upper()
    score = payload.get("risk_score", payload.get("score", 0))
    reason = str(payload.get("reason", ""))
    engine_state = _engine_state()
    age_s = payload.get("_age_s")
    updated_at = time.strftime("%H:%M:%S")
    video_age_ms = None
    try:
        video_age_ms = max(0, int(round((time.time() - DASHBOARD_PATH.stat().st_mtime) * 1000)))
    except OSError:
        pass
    compact = {
        "updated_at": updated_at,
        "video_age_ms": video_age_ms,
        "touchdesigner_state": "running",
        "engine": engine_state,
        "status_file": read_state,
        "state": state,
        "risk_score": score,
        "reason": reason,
        "status_age_s": round(float(age_s), 2) if isinstance(age_s, (int, float)) else None,
        "arduino_protocol": "managed by verified local sidecar",
        "machine_control_enabled": False,
    }
    _set_text("live_status", json.dumps(compact, ensure_ascii=False, indent=2))
    simulator = _read_computer_buzzer()
    simulator_display = dict(simulator)
    simulator_display["display_updated_at"] = updated_at
    stations = payload.get("stations", [])
    people = []
    if isinstance(stations, list):
        for station in stations:
            if isinstance(station, dict) and isinstance(station.get("people"), list):
                people.extend(station["people"])
    person = people[0] if people and isinstance(people[0], dict) else {}
    source_live = video_age_ms is not None and video_age_ms <= 1800
    using_computer_audio = bool(simulator.get("audio_enabled", False))
    # A muted simulator is not proof that an Arduino is connected.
    arduino_connected = False
    try:
        link = json.loads((RUNTIME_DIR / "arduino-connection.json").read_text(encoding="utf-8"))
        arduino_connected = link.get("connected") is True and 0 <= time.time() - float(link["updated_at"]) < 2.0
    except (OSError, ValueError, KeyError, TypeError):
        pass
    output_device = "Arduino UNO" if arduino_connected else "电脑声音" if using_computer_audio else "未就绪/静音"
    readable_reason = _reason_label(reason)
    simulation_line = (
        f"电脑蜂鸣器模拟：{simulator.get('pattern', '等待状态')}"
        if simulator
        else "电脑蜂鸣器模拟：正在启动（未接 UNO 时会自动发声）"
    )
    _set_text("computer_buzzer_note", json.dumps(simulator_display, ensure_ascii=False, indent=2) if simulator else simulation_line)
    _set_text(
        "risk_dashboard_to_arduino",
        "\n".join(
            [
                f"③ 风险决策数据  {updated_at}",
                f"状态：{state}",
                f"风险分数：{score}/100",
                f"待安全员审核：{pending_reviews} 条",
                "↓ 原始风险只入队，不直接触发声音",
            ]
        ),
    )
    _set_text(
        "operator_note",
        "\n".join(
            [
                f"更新时间：{updated_at}  画面年龄：{video_age_ms if video_age_ms is not None else '-'} ms",
                f"当前状态：{STATE_TITLES.get(state, state)}  {score}/100；待审核 {pending_reviews}",
                f"说明：{reason or '等待画面'}",
                f"引擎：{engine_state}  ｜  输入模式：{mode_name}",
                simulation_line,
                "提示：Arduino 蜂鸣器仅用于提醒，不连接机器控制。",
            ]
        ),
    )
    _set_card(
        "system_live_card",
        [
            f"SYSTEM / 系统  {updated_at}",
            f"视觉引擎 {engine_state}  ｜  状态数据 {read_state}",
            f"数据延迟 {round(float(age_s) * 1000) if isinstance(age_s, (int, float)) else '-'} ms  ｜  TD 实时运行",
            "检测范围 单工位  ｜  输出 仅提醒",
            "机器控制 关闭",
        ],
    )
    _set_card(
        "capture_live_card",
        [
            f"01 VIDEO INPUT  {updated_at}",
            f"萤石画面 {'LIVE' if source_live else 'WAITING'}  ｜  目标 20 fps",
            f"画面延迟 {video_age_ms if video_age_ms is not None else '-'} ms  ｜  屏幕窗口输入",
            "有效画面 1280 × 720  ｜  已去除 App 界面",
            "输出 纯监控画面",
        ],
    )
    _set_card(
        "vision_live_card",
        [
            f"02 OPENCV / POSE  {updated_at}",
            f"可见人员 {len(people)}  ｜  身体可见 {round(float(person.get('body_visibility', 0)) * 100)}%",
            f"低头变化 {person.get('head_drop_change', 0)}  ｜  躯干倾斜 {person.get('lean_change_deg', 0)}°",
            f"姿态信号 {person.get('posture_signal_count', 0)} / 3  ｜  全身模式",
            "分析范围 左侧单工位",
        ],
    )
    _set_card(
        "risk_live_card",
        [
            f"03 RISK / 原始建议  {updated_at}",
            f"当前状态 {state}  ｜  风险 {score} / 100",
            f"审核队列 {pending_reviews} 条  ｜  原始风险无声音权限",
            f"判断依据 {readable_reason}",
            "分级 0–44 正常｜45–69 趋势｜70–100 高风险",
        ],
    )
    _set_card(
        "arduino_live_card",
        [
            f"06 ARDUINO / 审核后输出  {updated_at}",
            f"UNO {'已连接' if arduino_connected else '未确认连接'}  ｜  {output_device}",
            f"审核等级 {review_level if review_level is not None else '-'}  ｜  输出授权 {'是' if review_authorized else '否'}",
            f"3–5级 两短一长（每次审核仅一组） ｜ 模式 {mode_name}",
            "串口 自动识别  ｜  机器控制关闭",
        ],
    )
    _set_card(
        "buzzer_live_card",
        [
            f"07 BUZZER / 人工闸门  {updated_at}",
            f"输出设备 {output_device}  ｜  风险 {score} / 100",
            f"当前节奏 {simulator.get('pattern', '等待状态')}",
            f"输出状态 {'已授权一次' if review_authorized else '静音'}  ｜  未审核/不确定/过期静音",
            f"声音源 仅 review-actuator.json ｜ TEST 实体输出禁用：{'是' if mode_name == 'TEST' else '否'}",
        ],
    )
    selected = review_state.get("selected") if isinstance(review_state.get("selected"), dict) else {}
    stats = review_state.get("statistics") if isinstance(review_state.get("statistics"), dict) else {}
    result = review_state.get("last_result") if isinstance(review_state.get("last_result"), dict) else {}
    _set_card(
        "review_live_card",
        [
            f"04–05 SAFETY OFFICER REVIEW  {updated_at}",
            f"服务 {review_state.get('service_state', 'STARTING')}  ｜  待审核 {pending_reviews}  ｜  今日已审 {stats.get('reviewed_count', 0)}",
            f"当前事件 {str(review_state.get('selected_event_id') or '-')[-18:]}  ｜  片段 {selected.get('clip_status', 'WAITING')}",
            f"系统 {selected.get('system_state', state)} / {selected.get('peak_score', score)}  ｜  人工等级 {review_level if review_level is not None else '-'}",
            f"{result.get('message', '等待安全员审核；蜂鸣器保持静音')}",
        ],
    )
    window = op(WINDOW_PATH)
    if window is not None:
        _set_value(window, "title", _title_for(payload, engine_state))


def runtime_start() -> None:
    """Start a single private vision sidecar with its existing Arduino bridge."""
    global _ENGINE_PROCESS
    network = _network()
    # Older builds stored a subprocess.Popen object in component storage.
    # TouchDesigner tries to pickle storage when saving, which produced a save
    # warning.  Process handles belong in module memory; only simple values go
    # into persistent operator storage.
    try:
        network.unstore("td_engine_process")
    except Exception:
        pass
    # UI commands are transient.  Never persist/replay a click from a previous
    # TouchDesigner session, especially a submit or actuator-related action.
    network.store("review_command_queue", [])
    network.store("review_active_command_id", "")
    network.store("review_active_command_since", 0.0)
    network.store("review_desired_input_mode", "")
    network.store("review_desired_input_mode_at", 0.0)
    _configure_visible_node_data()
    _configure_review_panel_layout()
    _hub, review = _review_nodes()
    _initialise_review_controls(review)
    # Review controls are polled by onFrameStart; a saved paused timeline
    # otherwise leaves clickable buttons disconnected from their actions.
    op("/").time.play = True
    _show_workflow()
    existing = _ENGINE_PROCESS
    if existing is not None and existing.poll() is None:
        return
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if not ENGINE_LAUNCHER.is_file():
        _set_text("live_status", f"启动器缺失：{ENGINE_LAUNCHER}")
        return
    process = subprocess.Popen(
        ["/bin/zsh", str(ENGINE_LAUNCHER)],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _ENGINE_PROCESS = process
    network.store("td_last_video_refresh", 0.0)
    network.store("td_last_status_refresh", 0.0)
    _set_text("live_status", "TouchDesigner 已启动后台识别引擎；等待萤石云视频窗口。")
    window = op(WINDOW_PATH)
    if window is not None:
        _pulse(window, "winopen", "open")


def runtime_tick() -> None:
    """Refresh live pixels quickly and slower text only when each is due."""
    network = _network()
    if network is None:
        return
    now = time.monotonic()
    last_video = float(network.fetch("td_last_video_refresh", 0.0))
    if now - last_video >= VIDEO_REFRESH_INTERVAL_S:
        network.store("td_last_video_refresh", now)
        _update_dashboard()
    last_status = float(network.fetch("td_last_status_refresh", 0.0))
    if now - last_status >= STATUS_REFRESH_INTERVAL_S:
        network.store("td_last_status_refresh", now)
        _update_review_ui()
        _publish_status()


def runtime_exit() -> None:
    """End both the monitor and its Arduino bridge when TD closes."""
    global _ENGINE_PROCESS
    network = _network()
    process = _ENGINE_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=4)
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass
    _ENGINE_PROCESS = None
