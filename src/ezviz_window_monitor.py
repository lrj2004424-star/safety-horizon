#!/usr/bin/env python3
"""Launch the configured whole-person fatigue observer on the EZVIZ window."""

from __future__ import annotations

import argparse
from pathlib import Path

import ezviz_multistation_fatigue_monitor


PROJECT_ROOT = Path(__file__).resolve().parent
EZVIZ_BUNDLE_ID = "com.tencent.yybmac.app.com.videogo"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-app", default=EZVIZ_BUNDLE_ID)
    parser.add_argument("--window-title")
    parser.add_argument('--source-config')
    parser.add_argument(
        "--window-helper",
        default=str(ezviz_multistation_fatigue_monitor.DEFAULT_WINDOW_HELPER),
    )
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument(
        "--full-window",
        action="store_true",
        help="Do not crop the top video player from the portrait EZVIZ app window",
    )
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "config" / "ezviz-fatigue-multistation.json"),
    )
    parser.add_argument(
        "--captures-dir",
        default=str(PROJECT_ROOT / "artifacts" / "ezviz-window-captures"),
    )
    parser.add_argument("--baseline-seconds", type=float, default=20.0)
    parser.add_argument("--trend-seconds", type=float, default=20.0)
    parser.add_argument("--high-risk-seconds", type=float, default=45.0)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without the OpenCV window; intended for TouchDesigner output.",
    )
    parser.add_argument(
        "--dashboard-output",
        help="Optional privacy-filtered JPEG path for an external live dashboard.",
    )
    parser.add_argument("--dashboard-output-fps", type=float, default=4.0)
    parser.add_argument(
        "--source-preview-output",
        help="Optional local-only raw video preview JPEG for the TouchDesigner workflow.",
    )
    parser.add_argument("--station-input-output", help="Optional left work-area input JPEG for TouchDesigner.")
    parser.add_argument("--vision-output", help="Optional OpenCV annotated JPEG for TouchDesigner.")
    parser.add_argument("--risk-card-output", help="Optional risk-decision JPEG for TouchDesigner.")
    parser.add_argument(
        "--input-mode-json",
        help="Optional LIVE/TEST input-mode control JSON consumed by the vision runtime.",
    )
    parser.add_argument(
        "--active-thresholds-json",
        help="Optional explicitly approved fatigue-threshold JSON.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Optional bounded run for diagnostics/tests (0 means continuous).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    captures_dir = Path(args.captures_dir).expanduser().resolve()
    manifest = ezviz_multistation_fatigue_monitor.load_wide_manifest(args.manifest)
    single_station = len(manifest.stations) == 1
    mode_label = "左侧单工位" if single_station else "上下双工位"
    print(f"\n萤石安全视界：{mode_label}整人疲劳风险观察模式")
    print("  无需 NVR IP、用户名或密码。")
    print("  请保持“萤石云视频”窗口打开并正在播放监控画面。")
    if single_station:
        print("  当前只检测左侧工位；右侧工位保留在备份清单中，不参与本次判断。")
    else:
        print("  上、下两个工位分别裁切、分别建立个人基线、分别判断。")
    print("  观察整个人的持续姿态与动作变化，手部不作为主要判断。")
    print("  S = 脱敏截图；R = 开始/停止脱敏录像；Q / Esc = 退出。")
    print("  首次运行如出现提示，请允许终端进行屏幕录制，然后重新打开本程序。\n")
    app_args = [
        "--manifest",
        str(Path(args.manifest).expanduser().resolve()),
        "--window-app",
        args.window_app,
        "--window-helper",
        str(Path(args.window_helper).expanduser().resolve()),
        "--fps",
        str(args.fps),
        "--captures-dir",
        str(captures_dir),
        "--status-json",
        str(captures_dir / "live-status.json"),
        "--baseline-seconds",
        str(args.baseline_seconds),
        "--trend-seconds",
        str(args.trend_seconds),
        "--high-risk-seconds",
        str(args.high_risk_seconds),
    ]
    if not args.full_window:
        app_args.extend(["--window-crop", "auto"])
    else:
        app_args.extend(["--window-crop", "full"])
    if args.window_title:
        app_args.extend(["--window-title", args.window_title])
    if args.headless:
        app_args.append("--headless")
    if args.dashboard_output:
        app_args.extend(["--dashboard-output", args.dashboard_output])
    if args.source_preview_output:
        app_args.extend(["--source-preview-output", args.source_preview_output])
    if args.station_input_output:
        app_args.extend(["--station-input-output", args.station_input_output])
    if args.vision_output:
        app_args.extend(["--vision-output", args.vision_output])
    if args.risk_card_output:
        app_args.extend(["--risk-card-output", args.risk_card_output])
    if args.input_mode_json:
        app_args.extend(["--input-mode-json", args.input_mode_json])
    if args.source_config:
        app_args.extend(['--source-config', args.source_config])
    if args.active_thresholds_json:
        app_args.extend(["--active-thresholds-json", args.active_thresholds_json])
    app_args.extend(["--dashboard-output-fps", str(args.dashboard_output_fps)])
    if args.max_frames:
        app_args.extend(["--max-frames", str(args.max_frames)])
    return ezviz_multistation_fatigue_monitor.main(app_args)


if __name__ == "__main__":
    raise SystemExit(main())
