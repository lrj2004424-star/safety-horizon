"""Create the 萤石安全视界 TouchDesigner版 .toe project.

Run this file inside TouchDesigner's Textport, not with normal Python.  It
uses TouchDesigner operator classes to create a portable network whose only
external files are in this same repository.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TD_DIR = PROJECT_ROOT / "touchdesigner"
# TouchDesigner’s internal saver on some macOS builds rejects a non-ASCII
# output filename.  Build to this ASCII staging path, then the installation
# script copies the verified .toe to its Chinese display name.
TOE_PATH = TD_DIR / "SafetyHorizon.toe"
RUNTIME_PATH = TD_DIR / "td_runtime.py"
DASHBOARD_PATH = PROJECT_ROOT / "runtime" / "touchdesigner-live-dashboard.jpg"
SOURCE_PREVIEW_PATH = PROJECT_ROOT / "runtime" / "touchdesigner-source-overview.jpg"
STATION_INPUT_PATH = PROJECT_ROOT / "runtime" / "touchdesigner-left-station-input.jpg"
VISION_RESULT_PATH = PROJECT_ROOT / "runtime" / "touchdesigner-opencv-result.jpg"
RISK_CARD_PATH = PROJECT_ROOT / "runtime" / "touchdesigner-risk-decision.jpg"
ANNOTATIONS_DIR = PROJECT_ROOT / "annotations"
REVIEW_STATE_PATH = PROJECT_ROOT / "runtime" / "safety-officer-review-state.json"
REVIEW_COMMAND_PATH = PROJECT_ROOT / "runtime" / "safety-officer-review-command.json"
VISION_SOURCE_PATH = PROJECT_ROOT / "ezviz_multistation_fatigue_monitor.py"
WINDOW_CAPTURE_BACKEND_PATH = PROJECT_ROOT / "safety_monitor" / "window_capture_device.py"
CAPTURE_SOURCE_PATH = PROJECT_ROOT / "ezviz_window_monitor.py"
ARDUINO_SOURCE_PATH = PROJECT_ROOT / "indicator_bridge.py"
COMPUTER_SIMULATOR_SOURCE_PATH = PROJECT_ROOT / "computer_buzzer_simulator.py"
CONTROLLER_SOURCE_PATH = TD_DIR / "td_runtime.py"
PYTHON_RUNTIME_SOURCE_PATH = PROJECT_ROOT / "start_touchdesigner_engine.command"
REVIEW_SERVICE_SOURCE_PATH = PROJECT_ROOT / "safety_officer_review.py"
REVIEW_SUPPORT_PATHS = (
    PROJECT_ROOT / "safety_monitor" / "review_contract.py",
    PROJECT_ROOT / "safety_monitor" / "review_controller.py",
    PROJECT_ROOT / "safety_monitor" / "review_store.py",
    PROJECT_ROOT / "safety_monitor" / "pre_event_buffer.py",
    PROJECT_ROOT / "safety_monitor" / "threshold_optimizer.py",
)


def safe_set(node, parameter: str, value) -> None:
    try:
        getattr(node.par, parameter).val = value
    except Exception:
        pass


def safe_pulse(node, parameter: str) -> None:
    try:
        getattr(node.par, parameter).pulse()
    except Exception:
        pass


def safe_node_size(node, width: int, height: int) -> None:
    """Make live TOPs visibly large in the network editor."""
    try:
        node.nodeWidth = width
        node.nodeHeight = height
    except Exception:
        pass


def configure_top_preview(node, width: int, height: int) -> None:
    """Keep a TOP's complete image visible in its network thumbnail.

    ``Fit Outside`` is useful for presentation panels but crops pixels in a
    network viewer.  Every evidence/status TOP in this project must instead
    use ``Fit Best`` so the operator graph never hides an edge of the frame or
    a line of text.
    """
    safe_node_size(node, width, height)
    safe_set(node, "fillmode", "best")
    safe_set(node, "filtertype", "linear")


def safe_connect(source, target, output_index: int = 0, input_index: int = 0) -> None:
    """Connect two operators while keeping the project builder repeatable."""
    try:
        source.outputConnectors[output_index].connect(target.inputConnectors[input_index])
    except Exception:
        pass


def fill_table(table, rows) -> None:
    """Replace a Table DAT with explicit rows."""
    try:
        table.clear()
        for row in rows:
            table.appendRow(row)
    except Exception:
        pass


def configure_panel(node, *, x: int, y: int, width: int, height: int) -> None:
    """Give Panel COMPs deterministic geometry inside the review terminal."""
    safe_set(node, "x", x)
    safe_set(node, "y", y)
    safe_set(node, "w", width)
    safe_set(node, "h", height)


def review_button(
    parent,
    name: str,
    label: str,
    *,
    node_x: int,
    node_y: int,
    panel_x: int,
    panel_y: int,
    panel_width: int,
    panel_height: int,
    mode: str = "command",
    group: str = "",
):
    """Create one clearly named operator-review button."""
    button = parent.create(buttonCOMP, name)
    button.nodeX, button.nodeY = node_x, node_y
    safe_node_size(button, max(150, panel_width), 70)
    configure_panel(
        button,
        x=panel_x,
        y=panel_y,
        width=panel_width,
        height=panel_height,
    )
    safe_set(button, "label", label)
    safe_set(button, "text", label)
    # The runtime polls at a deliberately modest rate.  Command buttons are
    # therefore latched (Toggle Up) and explicitly consumed/reset by the
    # runtime; a one-frame Momentary button could otherwise be missed.  Radio
    # and persistent toggles use TouchDesigner's documented menu names.
    button_type = {
        "command": "toggleup",
        "momentary": "toggleup",
        "toggle": "toggleup",
        "radio": "radioup",
    }.get(mode, mode)
    safe_set(button, "buttontype", button_type)
    safe_set(button, "value0", False)
    if group:
        # TouchDesigner uses a shared radio group to guarantee exactly one
        # selection.  The callback also mirrors the chosen value to the form
        # table so it remains visible and serialisable.
        safe_set(button, "buttongroup", group)
    safe_set(button, "scaletofit", "never")
    # Replace the default grey TouchDesigner widget with a restrained control
    # palette.  The colours communicate function without turning the review
    # terminal into a rainbow: navigation is slate, annotations are blue,
    # thresholds are amber, and the five grades progress green → red.
    rgb = (0.10, 0.15, 0.22)
    border = (0.20, 0.30, 0.42)
    if name.startswith("danger_level_"):
        grade = int(name.rsplit("_", 1)[-1])
        palette = {
            1: ((0.10, 0.28, 0.18), (0.25, 0.72, 0.42)),
            2: ((0.25, 0.25, 0.12), (0.78, 0.72, 0.24)),
            3: ((0.30, 0.20, 0.08), (0.92, 0.52, 0.16)),
            4: ((0.32, 0.10, 0.10), (0.90, 0.28, 0.24)),
            5: ((0.26, 0.08, 0.22), (0.84, 0.32, 0.74)),
        }
        rgb, border = palette.get(grade, (rgb, border))
    elif name in {"submit_review", "manual_review_capture", "acknowledge_and_silence"}:
        rgb, border = (0.08, 0.25, 0.35), (0.18, 0.66, 0.82)
    elif name in {"generate_threshold_advice", "apply_approved_thresholds", "run_simulation_test", "test_mode_switch"}:
        rgb, border = (0.22, 0.14, 0.30), (0.62, 0.38, 0.82)
    elif name.startswith("action_") or name.startswith("hand_zone_") or name == "material_occluded":
        rgb, border = (0.08, 0.18, 0.28), (0.16, 0.46, 0.68)
    elif name in {"previous_frame", "play_pause", "next_frame", "previous_alert", "next_alert"}:
        rgb, border = (0.10, 0.14, 0.20), (0.28, 0.38, 0.52)
    for channel, value in zip(("r", "g", "b"), rgb):
        safe_set(button, f"bgcolor{channel}", value)
    # Button COMP uses ``bgalpha`` for the fill alpha.  The similarly named
    # ``bgcolora`` parameter does not exist on this operator and silently
    # leaves the fill transparent (which made every control look grey/flat).
    safe_set(button, "bgalpha", 1.0)
    # Border colours are split into A/B edge colours on Button COMP.
    for param, value in (
        ("borderar", border[0]), ("borderag", border[1]), ("borderab", border[2]),
        ("borderbr", border[0]), ("borderbg", border[1]), ("borderbb", border[2]),
    ):
        safe_set(button, param, value)
    safe_set(button, "borderba", 1.0)
    safe_set(button, "bordera", 1.0)
    safe_set(button, "borderwidth", 1)
    safe_set(button, "fontsize", 14)
    safe_set(button, "fontcolorr", 0.92)
    safe_set(button, "fontcolorg", 0.95)
    safe_set(button, "fontcolorb", 0.98)
    button.comment = f"安全员操作：{label}"
    return button


def review_label(parent, name: str, label: str, *, panel_x: int, panel_y: int,
                 panel_width: int, panel_height: int, rgb=(0.06, 0.10, 0.16),
                 border=(0.14, 0.28, 0.40), fontsize: int = 16):
    """Create a non-interactive section label that renders in the Panel COMP.

    Text TOPs are useful as network documentation but are not composited by a
    Container COMP viewer.  These lightweight Button COMPs provide the
    operator-facing labels directly in the review terminal without entering
    the runtime's button action list.
    """
    label_comp = parent.create(buttonCOMP, name)
    label_comp.nodeX, label_comp.nodeY = panel_x - 1180, panel_y - 460
    safe_node_size(label_comp, max(120, panel_width), max(36, panel_height))
    configure_panel(label_comp, x=panel_x, y=panel_y,
                    width=panel_width, height=panel_height)
    safe_set(label_comp, "label", label)
    safe_set(label_comp, "text", label)
    safe_set(label_comp, "buttontype", "toggleup")
    safe_set(label_comp, "value0", False)
    safe_set(label_comp, "scaletofit", "never")
    for param, value in (("bgcolorr", rgb[0]), ("bgcolorg", rgb[1]),
                         ("bgcolorb", rgb[2]), ("bgalpha", 1.0),
                         ("borderar", border[0]), ("borderag", border[1]),
                         ("borderab", border[2]), ("borderbr", border[0]),
                         ("borderbg", border[1]), ("borderbb", border[2]),
                         ("borderba", 1.0), ("bordera", 1.0),
                         ("fontsize", fontsize), ("fontcolorr", 0.70),
                         ("fontcolorg", 0.86), ("fontcolorb", 0.96)):
        safe_set(label_comp, param, value)
    label_comp.comment = f"审核终端分区标题：{label}"
    return label_comp


def build_operator_hub(network):
    """Build the persistent human-review stage and its inspectable UI.

    Runtime code will populate these DATs and bind the buttons.  The topology
    itself belongs in this builder so rebuilding the .toe can never erase the
    audit terminal or accidentally restore a direct risk-to-buzzer route.
    """
    hub = network.create(containerCOMP, "operator_hub")
    hub.nodeX, hub.nodeY = 620, -690
    safe_node_size(hub, 520, 292)
    configure_panel(hub, x=0, y=0, width=1280, height=720)
    try:
        hub.viewer = True
        hub.activeViewer = True
    except Exception:
        pass
    hub.comment = (
        "安全员审核中转：原始风险只进入待审核队列；"
        "必须选择1–5级、行为标注并点击提交，才会生成 reviewed actuator 输出。"
    )

    raw_risk_in = hub.create(inDAT, "raw_risk_in")
    raw_risk_in.nodeX, raw_risk_in.nodeY = -760, 260
    raw_risk_in.comment = "输入：live_status 的原始状态、风险分数和原因。"
    review_trigger = hub.create(nullDAT, "review_trigger")
    review_trigger.nodeX, review_trigger.nodeY = -560, 260
    safe_connect(raw_risk_in, review_trigger)
    review_trigger.comment = (
        "状态变化触发器：FATIGUE_TREND / HIGH_RISK / UNCERTAIN "
        "进入待审核队列；连续相同状态去重。"
    )

    pending_index = hub.create(tableDAT, "pending_alert_index")
    pending_index.nodeX, pending_index.nodeY = -350, 260
    fill_table(
        pending_index,
        (
            ("event_id", "received_at", "station", "raw_state", "raw_score", "review_status", "clip_status"),
            ("等待实时预警", "-", "upper_station", "-", "0", "待审核", "等待缓存"),
        ),
    )
    pending_index.comment = "顶层队列索引；完整队列在 safety_officer_review/pending_alert_queue。"

    review = hub.create(containerCOMP, "safety_officer_review")
    review.nodeX, review.nodeY = -80, -80
    safe_node_size(review, 780, 438)
    configure_panel(review, x=0, y=0, width=1280, height=720)
    safe_set(review, "bgcolorr", 0.035)
    safe_set(review, "bgcolorg", 0.055)
    safe_set(review, "bgcolorb", 0.085)
    safe_set(review, "bgalpha", 1.0)
    try:
        review.viewer = True
        review.activeViewer = True
    except Exception:
        pass
    review.comment = (
        "安全员审核终端：待审队列、3–5秒片段回放、逐帧查看、"
        "行为/手部/遮挡标注、1–5级定级与提交。"
    )

    # Operator-facing hierarchy.  These labels are Panel COMPs so they are
    # visible in the running Container viewer (unlike standalone Text TOPs).
    review_label(
        review, "ui_title", "安全员审核中心   /   实时事件证据与人工处置",
        panel_x=24, panel_y=664, panel_width=1232, panel_height=40,
        rgb=(0.04, 0.13, 0.22), border=(0.10, 0.55, 0.78), fontsize=18,
    )
    review_label(
        review, "ui_queue_header", "01  待审核预警队列",
        panel_x=24, panel_y=520, panel_width=500, panel_height=34,
        rgb=(0.05, 0.09, 0.14), border=(0.12, 0.30, 0.42), fontsize=15,
    )
    review_label(
        review, "ui_evidence_header", "02  视频证据   前 3 秒 + 后 5 秒",
        panel_x=24, panel_y=332, panel_width=500, panel_height=34,
        rgb=(0.05, 0.09, 0.14), border=(0.12, 0.30, 0.42), fontsize=15,
    )
    review_label(
        review, "ui_decision_header", "03  人工判定与处置",
        panel_x=548, panel_y=520, panel_width=708, panel_height=34,
        rgb=(0.05, 0.09, 0.14), border=(0.12, 0.30, 0.42), fontsize=15,
    )
    review_label(
        review, "ui_grade_header", "危险等级（必须选择）",
        panel_x=548, panel_y=166, panel_width=708, panel_height=30,
        rgb=(0.08, 0.07, 0.09), border=(0.28, 0.20, 0.34), fontsize=14,
    )
    review_label(
        review, "ui_action_header", "行为 / 手部 / 遮挡标注",
        panel_x=548, panel_y=326, panel_width=708, panel_height=30,
        rgb=(0.05, 0.09, 0.14), border=(0.12, 0.30, 0.42), fontsize=14,
    )

    terminal_title = review.create(textTOP, "review_terminal_title")
    terminal_title.nodeX, terminal_title.nodeY = -1180, 580
    configure_top_preview(terminal_title, 620, 88)
    safe_set(terminal_title, "outputresolution", "custom")
    safe_set(terminal_title, "resolutionw", 1240)
    safe_set(terminal_title, "resolutionh", 176)
    safe_set(terminal_title, "outputaspect", "resolution")
    safe_set(terminal_title, "text", "SAFETY OFFICER REVIEW  /  安全员审核终端\n原始风险 → 视频证据 → 人工标注 → 分级处置")
    safe_set(terminal_title, "fontsize", 24)
    safe_set(terminal_title, "alignx", "left")
    safe_set(terminal_title, "aligny", "center")
    terminal_title.viewer = True

    pending_queue = review.create(tableDAT, "pending_alert_queue")
    pending_queue.nodeX, pending_queue.nodeY = -1180, 390
    safe_node_size(pending_queue, 760, 190)
    fill_table(
        pending_queue,
        (
            ("event_id", "time", "station", "system_state", "score", "reason", "status", "clip"),
            ("-", "-", "upper_station", "WAITING", "0", "等待风险状态变化", "待审核", "等待前3秒+后5秒缓存"),
        ),
    )
    pending_queue.comment = "按时间降序的待审核报警；选中一行后载入对应脱敏视频片段。"
    try:
        pending_queue.viewer = True
    except Exception:
        pass

    clip_waiting = review.create(textTOP, "review_clip_waiting")
    clip_waiting.nodeX, clip_waiting.nodeY = -1180, 120
    configure_top_preview(clip_waiting, 230, 130)
    safe_set(clip_waiting, "outputresolution", "custom")
    safe_set(clip_waiting, "resolutionw", 960)
    safe_set(clip_waiting, "resolutionh", 540)
    safe_set(clip_waiting, "outputaspect", "resolution")
    safe_set(clip_waiting, "text", "等待选择待审事件\n\n视频缓存完成后可逐帧回放\n未审核期间蜂鸣器保持静音")
    safe_set(clip_waiting, "fontsize", 31)
    safe_set(clip_waiting, "alignx", "center")
    safe_set(clip_waiting, "aligny", "center")
    safe_set(clip_waiting, "bgcolorr", 0.025)
    safe_set(clip_waiting, "bgcolorg", 0.032)
    safe_set(clip_waiting, "bgcolorb", 0.043)
    safe_set(clip_waiting, "bgalpha", 1.0)
    clip_waiting.viewer = True

    clip = review.create(moviefileinTOP, "review_clip_playback")
    clip.nodeX, clip.nodeY = -920, 120
    configure_top_preview(clip, 300, 170)
    safe_set(clip, "file", str(DASHBOARD_PATH))
    safe_set(clip, "playmode", "sequential")
    safe_set(clip, "play", False)
    clip.comment = (
        "3–5秒脱敏预警片段回放：选择队列记录后切换到对应 MP4；"
        "默认暂停，支持上一帧/播放暂停/下一帧。"
    )
    clip.viewer = True

    clip_display = review.create(switchTOP, "review_clip_display")
    clip_display.nodeX, clip_display.nodeY = -1180, -180
    configure_top_preview(clip_display, 620, 348)
    safe_connect(clip_waiting, clip_display, input_index=0)
    safe_connect(clip, clip_display, input_index=1)
    safe_set(clip_display, "index", 0)
    clip_display.comment = "审核证据唯一显示端：片段就绪前显示等待页，防止误看上一条事件。"
    clip_display.viewer = True

    review_button(
        review, "previous_frame", "◀ 上一帧",
        node_x=-1180, node_y=-90, panel_x=24, panel_y=626,
        panel_width=142, panel_height=48,
    )
    review_button(
        review, "play_pause", "▶ / Ⅱ 播放暂停",
        node_x=-1015, node_y=-90, panel_x=180, panel_y=626,
        panel_width=188, panel_height=48, mode="toggle",
    )
    review_button(
        review, "next_frame", "下一帧 ▶",
        node_x=-825, node_y=-90, panel_x=382, panel_y=626,
        panel_width=142, panel_height=48,
    )
    review_button(
        review, "previous_alert", "◀ 上一条预警",
        node_x=-1180, node_y=-170, panel_x=24, panel_y=568,
        panel_width=166, panel_height=44,
    )
    review_button(
        review, "next_alert", "下一条预警 ▶",
        node_x=-995, node_y=-170, panel_x=204, panel_y=568,
        panel_width=166, panel_height=44,
    )

    annotation_form = review.create(tableDAT, "annotation_form")
    annotation_form.nodeX, annotation_form.nodeY = -520, 390
    safe_node_size(annotation_form, 390, 180)
    fill_table(
        annotation_form,
        (
            ("field", "selected_value", "required"),
            ("danger_level", "", "yes"),
            ("action_type", "", "yes"),
            ("hand_zone", "cannot_judge", "yes"),
            ("material_occluded", "false", "yes"),
            ("review_note", "", "no"),
        ),
    )
    annotation_form.comment = "当前审核表单；单选组的最终值统一写到这里。"
    annotation_form.viewer = True

    grade_specs = (
        (1, "1  安全"),
        (2, "2  注意"),
        (3, "3  警告"),
        (4, "4  危险"),
        (5, "5  极度危险"),
    )
    for index, (grade, label) in enumerate(grade_specs):
        review_button(
            review,
            f"danger_level_{grade}",
            label,
            node_x=-520 + index * 165,
            node_y=215,
            panel_x=560 + index * 134,
            panel_y=102,
            panel_width=122,
            panel_height=48,
            mode="radio",
            group="danger_level",
        )

    action_specs = (
        ("action_normal_pick", "正常取料"),
        ("action_waste_cleanup", "清理废料"),
        ("action_violation", "违规动作"),
        ("action_style_change", "换款/换料"),
    )
    for index, (name, label) in enumerate(action_specs):
        review_button(
            review,
            name,
            label,
            node_x=-520 + index * 180,
            node_y=75,
            panel_x=560 + index * 166,
            panel_y=270,
            panel_width=150,
            panel_height=44,
            mode="radio",
            group="action_type",
        )

    hand_specs = (
        ("hand_zone_safe", "手部：安全区"),
        ("hand_zone_attention", "手部：注意区"),
        ("hand_zone_danger", "手部：危险区"),
        ("hand_zone_unknown", "手部：无法判断"),
    )
    for index, (name, label) in enumerate(hand_specs):
        review_button(
            review,
            name,
            label,
            node_x=-520 + index * 180,
            node_y=-65,
            panel_x=560 + index * 166,
            panel_y=214,
            panel_width=150,
            panel_height=44,
            mode="radio",
            group="hand_zone",
        )

    review_button(
        review, "material_occluded", "材料遮挡",
        node_x=-520, node_y=-205, panel_x=560, panel_y=362,
        panel_width=150, panel_height=44, mode="toggle",
    )
    review_button(
        review, "submit_review", "提交审核",
        node_x=-330, node_y=-205, panel_x=728, panel_y=362,
        panel_width=178, panel_height=44,
    )
    review_button(
        review, "manual_review_capture", "现场补录",
        node_x=-130, node_y=-205, panel_x=924, panel_y=362,
        panel_width=150, panel_height=44,
    )
    review_button(
        review, "acknowledge_and_silence", "确认 / 静音",
        node_x=50, node_y=-205, panel_x=1092, panel_y=362,
        panel_width=164, panel_height=44,
    )

    # The controller will bind all review controls to this one callback DAT.
    # Keeping a single callback surface avoids divergent logic in 15 buttons.
    callbacks = review.create(textDAT, "review_callbacks")
    callbacks.nodeX, callbacks.nodeY = 270, 390
    callbacks.text = """# 安全员审核回调入口（由 td_runtime 在启动时绑定）
# 1) 单选组互斥；2) 提交前验证；3) 原子写入标注；
# 4) 只有1–2级为静音，3–5级可生成审核后蜂鸣器事件。
"""
    callbacks.comment = "所有按钮共用的唯一回调入口；禁止各按钮复制业务逻辑。"

    review_records = review.create(tableDAT, "annotation_records")
    review_records.nodeX, review_records.nodeY = -1180, -310
    safe_node_size(review_records, 510, 150)
    fill_table(
        review_records,
        (("timestamp", "event_id", "station", "system_score", "system_state", "review_level", "action", "hand_zone", "occluded", "clip_path", "sample_class"),),
    )
    review_records.comment = "已提交标注的当日镜像；正式数据按日期+工位持久化到 annotations。"
    review_records.viewer = True

    annotation_folder = review.create(folderDAT, "annotation_storage")
    annotation_folder.nodeX, annotation_folder.nodeY = -650, -310
    safe_set(annotation_folder, "folder", str(ANNOTATIONS_DIR))
    annotation_folder.comment = "本地分类存储目录：annotations/YYYY-MM-DD/upper_station.json、JSONL 与 CSV 镜像。"

    annotation_writer = review.create(fileoutDAT, "annotation_file_out")
    annotation_writer.nodeX, annotation_writer.nodeY = -460, -310
    safe_connect(review_records, annotation_writer)
    safe_set(annotation_writer, "file", str(ANNOTATIONS_DIR / "current" / "upper_station.csv"))
    safe_set(annotation_writer, "active", False)
    annotation_writer.comment = "仅在提交审核时写出；运行时改为当天目录，禁止每帧写盘。"

    sample_classes = review.create(tableDAT, "sample_classification")
    sample_classes.nodeX, sample_classes.nodeY = -250, -310
    safe_node_size(sample_classes, 350, 150)
    fill_table(
        sample_classes,
        (
            ("sample_class", "rule", "today_count"),
            ("true_positive", "review 3-5 AND system alerted", "0"),
            ("false_positive", "review 1-2 AND system alerted", "0"),
            ("false_negative", "review 3-5 AND manual/periodic sample", "0"),
            ("true_negative", "review 1-2 AND manual/periodic sample", "0"),
        ),
    )
    sample_classes.comment = "正负样本分类；现场补录/定期低风险抽样用于补足漏报样本。"
    sample_classes.viewer = True

    threshold_table = review.create(tableDAT, "threshold_recommendations")
    threshold_table.nodeX, threshold_table.nodeY = 120, -310
    safe_node_size(threshold_table, 330, 150)
    fill_table(
        threshold_table,
        (
            ("parameter", "current", "suggested", "evidence", "apply_status"),
            ("trend_score", "45", "-", "insufficient reviewed data", "not_applied"),
            ("high_risk_score", "70", "-", "insufficient reviewed data", "not_applied"),
            ("trend_seconds", "20", "-", "manual approval required", "not_applied"),
        ),
    )
    threshold_table.comment = "每周阈值建议：只显示建议与样本量，不会未经人工确认就改变在线风险引擎。"
    threshold_table.viewer = True

    review_button(
        review, "generate_threshold_advice", "生成阈值建议",
        node_x=470, node_y=-310, panel_x=560, panel_y=448,
        panel_width=188, panel_height=46,
    )
    review_button(
        review, "apply_approved_thresholds", "审批后应用",
        node_x=670, node_y=-310, panel_x=764, panel_y=448,
        panel_width=166, panel_height=46,
    )

    test_video = review.create(moviefileinTOP, "simulation_video_input")
    test_video.nodeX, test_video.nodeY = 870, -310
    configure_top_preview(test_video, 320, 180)
    safe_set(test_video, "file", str(DASHBOARD_PATH))
    safe_set(test_video, "play", False)
    test_video.comment = "事故模拟视频测试输入；未选择素材时保持暂停，不会覆盖实时输入。"
    try:
        test_video.viewer = True
    except Exception:
        pass
    review_button(
        review, "test_mode_switch", "测试模式：关",
        node_x=1190, node_y=-310, panel_x=948, panel_y=448,
        panel_width=186, panel_height=46, mode="toggle",
    )
    review_button(
        review, "run_simulation_test", "运行模拟测试",
        node_x=1390, node_y=-310, panel_x=1150, panel_y=448,
        panel_width=106, panel_height=46,
    )
    test_log = review.create(tableDAT, "simulation_test_log")
    test_log.nodeX, test_log.nodeY = 870, -500
    safe_node_size(test_log, 510, 140)
    fill_table(
        test_log,
        (("timestamp", "video", "expected_level", "detected_state", "detected_score", "match", "notes"),),
    )
    test_log.comment = "模拟素材测试日志；与真实工位标注分开存储，不混入现场准确率。"
    test_log.viewer = True

    stats = review.create(tableDAT, "today_review_stats")
    stats.nodeX, stats.nodeY = -1180, -500
    safe_node_size(stats, 420, 140)
    fill_table(
        stats,
        (
            ("metric", "value"),
            ("pending_reviews", "0"),
            ("reviewed_today", "0"),
            ("valid_alerts", "0"),
            ("false_positives", "0"),
            ("review_coverage", "0%"),
            ("accuracy_when_labeled", "N/A"),
        ),
    )
    stats.comment = "今日审核统计数据源；未标注样本不计入准确率。"
    stats.viewer = True

    stats_panel = review.create(textTOP, "review_statistics_panel")
    stats_panel.nodeX, stats_panel.nodeY = -740, -500
    configure_top_preview(stats_panel, 460, 184)
    safe_set(stats_panel, "outputresolution", "custom")
    safe_set(stats_panel, "resolutionw", 920)
    safe_set(stats_panel, "resolutionh", 368)
    safe_set(stats_panel, "outputaspect", "resolution")
    safe_set(
        stats_panel,
        "text",
        "TODAY / 今日审核\n\n待审核  0     已审核  0\n有效报警  0   误报  0\n1–5级分布  0 / 0 / 0 / 0 / 0\n标注覆盖率  0%   准确率  N/A",
    )
    safe_set(stats_panel, "fontsize", 19)
    safe_set(stats_panel, "alignx", "left")
    safe_set(stats_panel, "aligny", "top")
    stats_panel.comment = "安全员一眼可见的当日数据面板；由标注存储增量刷新。"
    stats_panel.viewer = True

    reviewed_state = hub.create(tableDAT, "reviewed_actuator_state")
    reviewed_state.nodeX, reviewed_state.nodeY = 720, 260
    fill_table(
        reviewed_state,
        (
            ("review_status", "review_level", "source_state", "source_score", "buzzer_allowed", "buzzer_level", "reason"),
            ("pending", "0", "UNCERTAIN", "0", "false", "0", "waiting_for_safety_officer_review"),
        ),
    )
    reviewed_state.comment = (
        "唯一可输出到蜂鸣器的审核后状态：1–2级静音；"
        "3–5级可提醒；待审核、UNCERTAIN、过期状态都静音。"
    )
    safe_node_size(reviewed_state, 680, 120)
    reviewed_state.viewer = True
    reviewed_out = hub.create(outDAT, "reviewed_alert_out")
    reviewed_out.nodeX, reviewed_out.nodeY = 930, 260
    safe_connect(reviewed_state, reviewed_out)
    reviewed_out.comment = "输出：经安全员确认的提醒状态；不传递未审核的原始蜂鸣等级。"
    # Show the selected evidence clip on the top-level operator_hub thumbnail.
    # Double-clicking the node still exposes every review/control/data operator.
    safe_set(review, "backgroundtop", clip_display.path)
    safe_set(hub, "backgroundtop", clip_display.path)
    return hub


root = op("/")
if root.op("safety_td") or root.op("safety_window"):
    raise RuntimeError("Use a NEW blank TD project. Existing workflows are never overwritten.")

network = root.create(baseCOMP, "safety_td")
network.nodeX = 0
network.nodeY = 0
network.comment = "萤石安全视界总工作流：视频导入 → OpenCV风险建议 → 安全员审核 → 审核后提醒；原始风险绝不直连蜂鸣器，且绝不控制机器。"

readme = network.create(textDAT, "README")
readme.nodeX, readme.nodeY = -720, 360
readme.text = """萤石安全视界 TouchDesigner版（左侧单工位）

双击桌面 .app 后会自动：
1. 打开萤石云，只提取播放器内的纯视频画面；
2. 启动左侧单工位的全身姿态、动作和疲劳趋势识别；
3. 在 1280x720 专业监控台左侧显示实时画面，右侧显示逐项数据；
4. 风险先进入安全员待审核队列；人工评定 3–5 级后，UNO/电脑只播放一次“两短一长”。

锁屏/黑帧/无法看清：显示等待页并静音，画面恢复后自动继续。
本系统仅作风险提醒，绝不控制机器。

原“萤石安全视界.app”保持不变，作为备份。
"""

operator_note = network.create(textDAT, "operator_note")
operator_note.nodeX, operator_note.nodeY = 330, -260
operator_note.text = "正在等待后台识别引擎…"

# These DATs make the complete operational logic inspectable directly in
# TouchDesigner.  The runtime still uses the verified local Python process for
# OpenCV/MediaPipe because TD's bundled interpreter does not ship those native
# packages, but TD remains the single visible controller.
code_overview = network.create(textDAT, "SYSTEM_LOGIC")
code_overview.nodeX, code_overview.nodeY = -720, 520
code_overview.text = """系统逻辑（TouchDesigner 为总控）

0. Python 3.14.app：用户双击的启动入口（已安装）。
1. python_vision_runtime：已验证的 Python 3.12.14 视觉运行环境。
2. source_overview：萤石云原始总览画面（实时输入，仅本机预览）。
3. capture_source：定位萤石窗口，用16:9“总览+检测区放大”显示实际裁切范围。
4. vision_engine：用16:9“骨架+数据”页面显示左侧工位的可见度、头部、躯干、肩线、动作和姿态信号。
5. live_dashboard / risk_dashboard：仅左侧工位的脱敏实时识别画面。
6. operator_hub/safety_officer_review：待审队列、前3秒+后5秒脱敏回放、行为标注、1–5级人工评定、每日JSON/JSONL/CSV和统计。
7. reviewed_alert_out：唯一声音闸门；1–2级/未审核/UNCERTAIN/过期静音，3–5级一次“两短一长”。
8. arduino_alert：只读取审核闸门；未接 UNO 时电脑模拟同一声音。
9. threshold_recommendations / simulation_video_input：阈值建议需审批，测试模式禁止实体输出。
10. lifecycle：由 TouchDesigner 启动、刷新和停止以上识别引擎。

为何有外部 Python：OpenCV 和 MediaPipe 是原生视觉库，TouchDesigner
自带 Python 不含这些库。它们由本项目已验证的 Python 运行环境执行；
启动、停止、画面、状态和 Arduino 协议都由本 TouchDesigner 文件管理。
"""
code_overview.comment = "总览：从左到右看视频；从下到上看风险状态与 Arduino 提醒。"

def source_dat(name: str, source_path: Path, x: int, y: int, label: str):
    dat = network.create(textDAT, name)
    dat.nodeX, dat.nodeY = x, y
    try:
        dat.text = f"# {label}\n# 双击此节点可查看完整源码。\n\n" + source_path.read_text(encoding="utf-8")
    except Exception as error:
        dat.text = f"无法读取 {source_path.name}: {error}"
    dat.comment = f"源码镜像：{source_path.name}。双击可查看完整代码。"
    return dat

python_runtime = source_dat("python_vision_runtime", PYTHON_RUNTIME_SOURCE_PATH, -720, 300, "Python 3.14.app 启动入口 → Python 3.12.14 OpenCV 运行环境")
python_runtime.comment = "Python 3.14.app 是用户的启动入口；目前 OpenCV/MediaPipe 已验证在 Python 3.12.14 中运行。此脚本启动屏幕捕捉、识别与 Arduino 桥接。"
capture_source = source_dat("capture_source", CAPTURE_SOURCE_PATH, -720, 110, "① 萤石云屏幕捕捉代码")
capture_source.comment = "① Python 视觉环境调用此代码：抓取电脑屏幕中正在显示的萤石云视频窗口。"
vision_source = source_dat("vision_engine", VISION_SOURCE_PATH, -380, 110, "② OpenCV + MediaPipe 全身疲劳识别代码")
try:
    vision_source.text += (
        "\n\n# " + "=" * 76 +
        "\n# 萤石可见窗口捕捉与播放器裁切后端（同一工作流的实际输入代码）\n# " + "=" * 76 + "\n\n" +
        WINDOW_CAPTURE_BACKEND_PATH.read_text(encoding="utf-8")
    )
except Exception as error:
    vision_source.text += f"\n\n# 无法读取窗口捕捉后端：{error}\n"
vision_source.comment = "② 可查看完整主视觉引擎与窗口捕捉后端：纯播放器裁切、全身骨架、动作节律、疲劳趋势和风险评分。"
review_runtime_source = source_dat("safety_officer_review_runtime", REVIEW_SERVICE_SOURCE_PATH, -40, 110, "④–⑤ 安全员审核、视频缓存、标注存储与阈值建议代码")
for support_path in REVIEW_SUPPORT_PATHS:
    try:
        review_runtime_source.text += (
            "\n\n# " + "=" * 76
            + f"\n# {support_path.name}\n# " + "=" * 76 + "\n\n"
            + support_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        review_runtime_source.text += f"\n\n# 无法读取 {support_path.name}：{error}\n"
review_runtime_source.comment = "④–⑤ 完整人工审核闸门源码镜像；双击可查看队列、缓存、标注、统计和阈值建议的实现。"
arduino_source = source_dat("arduino_alert", ARDUINO_SOURCE_PATH, 10, -300, "⑥ Arduino UNO 审核后蜂鸣器代码")
arduino_source.comment = "⑥ 仅读取未过期的 HUMAN_REVIEW 授权；经 USB 串口向 Arduino UNO 发送一次两短一长提醒。"
computer_simulator_source = source_dat("computer_buzzer_simulator_code", COMPUTER_SIMULATOR_SOURCE_PATH, 10, -480, "⑦ 未接 UNO 时的电脑蜂鸣器模拟代码")
computer_simulator_source.comment = "⑦ 无硬件模拟：只读取与 Arduino 相同的审核后命令；未检测到 UNO 时在电脑扬声器播放同节奏提醒。"
controller_source = source_dat("td_controller", CONTROLLER_SOURCE_PATH, 330, 330, "TouchDesigner 总控代码")
controller_source.comment = "TouchDesigner 总控：启动 Python 视觉环境、读取实时画面/状态、刷新本工作流。"

live_status = network.create(textDAT, "live_status")
live_status.nodeX, live_status.nodeY = 300, -300
live_status.text = "{}"
live_status.comment = "③ 原始风险证据：与 risk_dashboard 使用同一份 OpenCV 结果，只能进入审核队列，不能直接触发声音。"

waiting = network.create(textTOP, "waiting_screen")
waiting.nodeX, waiting.nodeY = -250, 220
configure_top_preview(waiting, 260, 146)
safe_set(waiting, "outputresolution", "custom")
safe_set(waiting, "resolutionw", 1280)
safe_set(waiting, "resolutionh", 720)
safe_set(waiting, "outputaspect", "resolution")
safe_set(waiting, "text", "萤石安全视界 TouchDesigner版\n\n正在等待‘萤石云视频’实时画面…\n\n若电脑已锁屏，请解锁；画面会自动恢复。\n等待与不确定状态始终静音。")
safe_set(waiting, "fontsize", 42)
waiting.comment = "等待画面：只有在萤石云视频没有可捕捉的播放页时才显示；不代表程序崩溃。"

source_overview = network.create(moviefileinTOP, "source_overview")
source_overview.nodeX, source_overview.nodeY = -1320, 40
configure_top_preview(source_overview, 384, 216)
safe_set(source_overview, "file", str(SOURCE_PREVIEW_PATH))
source_overview.comment = "0 原始实时总览输入：萤石云播放页中实际送入 OpenCV 的视频区域。仅本机 TouchDesigner 实时预览，文件持续覆盖，不作为事件素材保存。"

raw_to_opencv = network.create(nullTOP, "raw_video_to_opencv")
raw_to_opencv.nodeX, raw_to_opencv.nodeY = -930, 95
configure_top_preview(raw_to_opencv, 160, 90)
source_overview.outputConnectors[0].connect(raw_to_opencv.inputConnectors[0])
raw_to_opencv.comment = "1 原始视频输入 → Python/OpenCV：这条线显示真实输入方向；OpenCV 在外部 Python 视觉引擎中执行。"

dashboard = network.create(moviefileinTOP, "live_dashboard")
dashboard.nodeX, dashboard.nodeY = -730, 40
configure_top_preview(dashboard, 384, 216)
safe_set(dashboard, "file", str(STATION_INPUT_PATH))
dashboard.comment = "① 摄像头输入与工位定位：左侧显示整个播放器及高亮 ROI，右侧放大唯一检测区并显示分辨率、画面覆盖率和候选人数。"

# Visible video-processing route. The OpenCV/MediaPipe inference occurs in the
# verified Python runtime, while these TOPs expose its live result in TD.
opencv_result = network.create(moviefileinTOP, "opencv_fatigue_result")
opencv_result.nodeX, opencv_result.nodeY = -340, 25
configure_top_preview(opencv_result, 448, 252)
safe_set(opencv_result, "file", str(VISION_RESULT_PATH))
opencv_result.comment = "② OpenCV 可视化分析：左侧显示脱敏全身骨架，右侧实时显示轨迹、身体可见度、低头、躯干倾斜、肩线倾斜、全身动作和姿态信号。"

switch = network.create(switchTOP, "dashboard_switch")
switch.nodeX, switch.nodeY = 95, 95
configure_top_preview(switch, 176, 99)
waiting.outputConnectors[0].connect(switch.inputConnectors[0])
safe_set(switch, "index", 0)
switch.comment = "画面选择：有实时视频时显示最终风险仪表板；无视频时显示等待提示。"

risk_hud = network.create(moviefileinTOP, "risk_dashboard")
risk_hud.nodeX, risk_hud.nodeY = 270, 25
configure_top_preview(risk_hud, 448, 252)
safe_set(risk_hud, "file", str(RISK_CARD_PATH))
risk_hud.comment = "③ 系统风险建议：显示左侧工位分数和状态；这是待审核证据，不是蜂鸣器命令。"

display = network.create(moviefileinTOP, "dashboard_display")
display.nodeX, display.nodeY = 710, 0
configure_top_preview(display, 512, 288)
safe_set(display, "file", str(DASHBOARD_PATH))
display.comment = "④ 最终实时输出：左边只显示脱敏画面+骨架；右边显示头部、躯干、肩线、动作、可见度、趋势和蜂鸣器数据。"
display.outputConnectors[0].connect(switch.inputConnectors[1])

# Visual-only workflow mirror.  These TOPs make the processing order obvious
# in the network editor without rerouting any live data or changing the
# already-tested runtime connections below.  The actual OpenCV and review
# logic remains in the Python/DAT chain; this route is deliberately display
# only and is safe to reconnect when the project is rebuilt.
flow_capture = network.create(switchTOP, "flow_capture_stage")
flow_capture.nodeX, flow_capture.nodeY = -1320, 420
configure_top_preview(flow_capture, 220, 124)
safe_set(flow_capture, "index", 0)
flow_capture.comment = "可视化流程 01｜萤石窗口/视频采集（镜像连线，不改变运行）"

flow_analysis = network.create(switchTOP, "flow_analysis_stage")
flow_analysis.nodeX, flow_analysis.nodeY = -850, 420
configure_top_preview(flow_analysis, 220, 124)
safe_set(flow_analysis, "index", 0)
flow_analysis.comment = "可视化流程 02｜Python + OpenCV / MediaPipe 分析（镜像连线）"

flow_decision = network.create(switchTOP, "flow_decision_stage")
flow_decision.nodeX, flow_decision.nodeY = -380, 420
configure_top_preview(flow_decision, 220, 124)
safe_set(flow_decision, "index", 0)
flow_decision.comment = "可视化流程 03｜风险评分与状态判断（镜像连线）"

flow_review = network.create(switchTOP, "flow_review_stage")
flow_review.nodeX, flow_review.nodeY = 90, 420
configure_top_preview(flow_review, 220, 124)
safe_set(flow_review, "index", 0)
flow_review.comment = "可视化流程 04｜安全员审核/行为标注（镜像连线）"

flow_actuation = network.create(switchTOP, "flow_actuation_stage")
flow_actuation.nodeX, flow_actuation.nodeY = 560, 420
configure_top_preview(flow_actuation, 220, 124)
safe_set(flow_actuation, "index", 0)
flow_actuation.comment = "可视化流程 05｜审核后 Arduino/电脑蜂鸣器（镜像连线）"

safe_connect(source_overview, flow_capture, input_index=0)
safe_connect(raw_to_opencv, flow_capture, input_index=1)
safe_connect(flow_capture, flow_analysis, input_index=0)
safe_connect(opencv_result, flow_analysis, input_index=1)
safe_connect(flow_analysis, flow_decision, input_index=0)
safe_connect(risk_hud, flow_decision, input_index=1)
safe_connect(flow_decision, flow_review, input_index=0)
safe_connect(display, flow_review, input_index=1)
safe_connect(flow_review, flow_actuation, input_index=0)
safe_connect(display, flow_actuation, input_index=1)

risk_to_arduino_link = network.create(textDAT, "risk_dashboard_to_arduino")
risk_to_arduino_link.nodeX, risk_to_arduino_link.nodeY = 300, -185
risk_to_arduino_link.text = "③ 原始风险证据\n↓ ④ 安全员队列 + 前后视频回放\n↓ ⑤ 1–5级评定与行为标注\n↓ ⑥ 审核闸门 → 蜂鸣器"
risk_to_arduino_link.comment = "真实控制顺序：OpenCV 原始风险只入队；安全员提交后才生成 reviewed actuator。"

# Keep the actual video visible inside the network graph, not only in the
# separate Window COMP output.
for live_top in (source_overview, raw_to_opencv, dashboard, opencv_result, switch, risk_hud, display):
    try:
        live_top.viewer = True
    except Exception:
        pass

# Visible status → Arduino route. The bridge stays inside the verified Python
# runtime, but TouchDesigner owns the state source and exposes the trigger.
risk_event = network.create(nullDAT, "risk_event")
risk_event.nodeX, risk_event.nodeY = 480, -300
live_status.outputConnectors[0].connect(risk_event.inputConnectors[0])
risk_event.comment = "④ 原始风险事件：只允许进入安全员审核队列，禁止直接连接 Arduino 或电脑蜂鸣器。"

operator_hub = build_operator_hub(network)
safe_connect(risk_event, operator_hub)
operator_hub.comment += " 顶层真实连线：risk_event → operator_hub → reviewed_alert_out。"

arduino_trigger = network.create(nullDAT, "arduino_buzzer_trigger")
arduino_trigger.nodeX, arduino_trigger.nodeY = 1200, -300
safe_connect(operator_hub, arduino_trigger)
arduino_trigger.comment = "⑥ Arduino 审核后触发：只读取 review-actuator.json。未审核/1–2级/UNCERTAIN/过期=静音；人工3–5级=一次两短一长。"

computer_buzzer = network.create(nullDAT, "computer_buzzer_simulator")
computer_buzzer.nodeX, computer_buzzer.nodeY = 1370, -300
arduino_trigger.outputConnectors[0].connect(computer_buzzer.inputConnectors[0])
computer_buzzer.comment = "⑦ 电脑蜂鸣器模拟器：未接 UNO 时读取同一份审核后命令；每个审核决定只播放一次两短一长。"

computer_buzzer_note = network.create(textDAT, "computer_buzzer_note")
computer_buzzer_note.nodeX, computer_buzzer_note.nodeY = 1370, -430
computer_buzzer_note.text = "电脑蜂鸣器模拟：正在启动…"
computer_buzzer_note.comment = "模拟器状态：会显示当前模拟的声音节奏。它只模拟提醒，不会控制任何机器。"

lifecycle = network.create(executeDAT, "lifecycle")
lifecycle.nodeX, lifecycle.nodeY = 330, 150
lifecycle.text = f'''from pathlib import Path
RUNTIME_PATH = str(Path(project.folder) / "td_runtime.py")
_runtime_scope = {{"__file__": RUNTIME_PATH, "op": op}}
with open(RUNTIME_PATH, "r", encoding="utf-8") as _runtime_file:
    exec(compile(_runtime_file.read(), RUNTIME_PATH, "exec"), _runtime_scope, _runtime_scope)

def onStart():
    _runtime_scope["runtime_start"]()
    return

def onFrameStart(frame):
    _runtime_scope["runtime_tick"]()
    return

def onExit():
    _runtime_scope["runtime_exit"]()
    return
'''
for parameter in ("start", "framestart", "exit"):
    safe_set(lifecycle, parameter, True)
lifecycle.comment = "总控：TouchDesigner 打开时启动识别；每帧刷新视频与风险；退出时关闭识别和 Arduino 桥接。"

window = root.create(windowCOMP, "safety_window")
window.nodeX, window.nodeY = 600, 0
# In current TouchDesigner builds this parameter is named ``winop``.  Point
# the output at the live/waiting switch so an old JPEG can never remain on
# screen when the EZVIZ source is closed or unavailable.
safe_set(window, "winop", switch.path)
safe_set(window, "title", "萤石安全视界 TouchDesigner版")
safe_set(window, "borders", True)
safe_set(window, "winw", 1280)
safe_set(window, "winh", 720)
window.comment = "面向日常使用的 TouchDesigner 输出窗口；无新帧时显示等待页，不显示旧截图。"

# Presentation layout only.  Keep one left-to-right processing lane, a
# separate review/actuation lane, and a compact status-card row.  Positions do
# not affect operator execution or any of the connections above.
presentation_layout = {
    # system / documentation row
    "SYSTEM_LOGIC": (-1180, 760), "README": (-900, 760),
    "python_vision_runtime": (-620, 760), "capture_source": (-340, 760),
    "vision_engine": (-60, 760), "waiting_screen": (220, 760),
    "lifecycle": (500, 760), "td_controller": (780, 760),
    # live video and analysis lane
    "source_overview": (-1280, 180), "raw_video_to_opencv": (-970, 180),
    "live_dashboard": (-680, 180), "opencv_fatigue_result": (-320, 180),
    "dashboard_switch": (40, 180), "risk_dashboard": (300, 180),
    "dashboard_display": (650, 180),
    # visual process mirror (TOP-only, non-invasive)
    "flow_capture_stage": (-1120, 470), "flow_analysis_stage": (-700, 470),
    "flow_decision_stage": (-280, 470), "flow_review_stage": (140, 470),
    "flow_actuation_stage": (560, 470),
    # reviewer / actuator lane
    "live_status": (-260, -200), "risk_event": (20, -200),
    "operator_hub": (330, -200), "arduino_buzzer_trigger": (780, -200),
    "computer_buzzer_simulator": (1160, -200),
    "computer_buzzer_note": (1160, -390),
    "risk_dashboard_to_arduino": (40, -390),
    # compact status cards
    "system_live_card": (-1150, -590), "capture_live_card": (-780, -590),
    "vision_live_card": (-410, -590), "risk_live_card": (-40, -590),
    "arduino_live_card": (330, -590), "buzzer_live_card": (700, -590),
    "review_live_card": (1070, -590),
    "arduino_alert": (330, -790),
    "computer_buzzer_simulator_code": (700, -790),
    "operator_note": (1070, -790),
}
for _name, (_x, _y) in presentation_layout.items():
    try:
        _node = network.op(_name)
        if _node is not None:
            _node.nodeX, _node.nodeY = _x, _y
    except Exception:
        pass

# Persist the visible safety graph as the startup page.  The user can still
# open the separate Window COMP when needed, but the normal project view is
# the annotated workflow with live video previews.
try:
    for pane in ui.panes:
        if str(getattr(pane, "type", "")) == "PaneType.NETWORKEDITOR":
            pane.owner = network
            pane.showBackdropTOPs = True
            pane.home()
            break
except Exception:
    pass

project.save(str(TOE_PATH))
print(f"TOUCHDESIGNER_PROJECT_CREATED: {TOE_PATH}")
