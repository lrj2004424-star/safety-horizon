#!/usr/bin/env python3
"""Friendly secure launcher for an EZVIZ/Hikvision NVR live channel."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
from urllib.parse import quote

import app as monitor_app
import wide_multistation as wide_monitor_app


PROJECT_ROOT = Path(__file__).resolve().parent
INTERNAL_SOURCE_ENV = "CUTTING_SAFETY_EZVIZ_SOURCE"


def build_rtsp_url(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    channel: int,
    stream: int,
) -> str:
    """Build a DS-series NVR channel URL with safely encoded credentials."""
    clean_host = host.strip().strip("[]")
    if not clean_host or any(character in clean_host for character in "/?#@"):
        raise ValueError("NVR host must be a bare IP address or hostname")
    if not 1 <= int(port) <= 65535:
        raise ValueError("RTSP port must be between 1 and 65535")
    if not username:
        raise ValueError("NVR username is required")
    if int(channel) < 1:
        raise ValueError("channel must be positive")
    if int(stream) not in (1, 2):
        raise ValueError("stream must be 1 (main) or 2 (sub)")
    host_part = f"[{clean_host}]" if ":" in clean_host else clean_host
    channel_code = f"{int(channel)}{int(stream):02d}"
    return (
        f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}@"
        f"{host_part}:{int(port)}/Streaming/channels/{channel_code}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--source-env",
        help="Environment variable already containing the complete RTSP URL",
    )
    source.add_argument("--host", help="NVR LAN IP address or hostname")
    parser.add_argument("--port", type=int, default=554, help="NVR RTSP port")
    parser.add_argument("--username", help="NVR account name; prompted when omitted")
    parser.add_argument(
        "--password-env",
        help="Environment variable containing the NVR password; otherwise prompt securely",
    )
    parser.add_argument("--channel", type=int, default=32, help="NVR channel number")
    parser.add_argument(
        "--substream",
        action="store_true",
        help="Use stream 02 for lower bandwidth; main stream 01 is the default",
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "station.example.json"),
    )
    parser.add_argument(
        "--wide-manifest",
        help="Split one wide stream into independent workstation pipelines",
    )
    parser.add_argument(
        "--captures-dir",
        default=str(PROJECT_ROOT / "artifacts" / "ezviz-live-captures"),
    )
    parser.add_argument(
        "--calibrated",
        action="store_true",
        help="Enable risk-zone decisions only after this exact camera view is calibrated",
    )
    parser.add_argument("--mute", action="store_true")
    parser.add_argument("--auto-capture-states", default="WARNING,DANGER")
    parser.add_argument("--event-clip-seconds", type=float, default=10.0)
    return parser.parse_args(argv)


def _prompt(value: str | None, label: str) -> str:
    return value.strip() if value else input(label).strip()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.source_env:
        if not os.environ.get(args.source_env):
            raise ValueError(f"Environment variable {args.source_env!r} is missing or empty")
        source_env = args.source_env
    else:
        host = _prompt(args.host, "NVR 局域网 IP：")
        username = _prompt(args.username, "NVR 用户名：")
        if args.password_env:
            password = os.environ.get(args.password_env, "")
            if not password:
                raise ValueError(f"Environment variable {args.password_env!r} is missing or empty")
        else:
            password = getpass.getpass("NVR 密码（输入时不显示）：")
        os.environ[INTERNAL_SOURCE_ENV] = build_rtsp_url(
            host=host,
            port=args.port,
            username=username,
            password=password,
            channel=args.channel,
            stream=2 if args.substream else 1,
        )
        source_env = INTERNAL_SOURCE_ENV

    captures_dir = Path(args.captures_dir).expanduser().resolve()
    print("\n萤石/NVR 实时监看已准备：")
    print("  S = 保存当前标注截图（人脸模糊）")
    print("  R = 开始/停止手动录像（人脸模糊）")
    print("  Q / Esc = 退出")
    if args.wide_manifest:
        print("  模式 = 单广角多工位；每个裁切区独立识别和判断")
    elif args.calibrated:
        print("  模式 = 已标定风险识别；WARNING/DANGER 自动抓拍并录制事件短片")
    else:
        print("  模式 = 高位广角观察；未标定前不声称安全或危险")
    print(f"  捕捉目录 = {captures_dir}\n")

    common_args = [
        "--source-env",
        source_env,
        "--captures-dir",
        str(captures_dir),
        "--status-json",
        str(captures_dir / "live-status.json"),
        "--auto-capture-states",
        args.auto_capture_states,
        "--event-clip-seconds",
        str(args.event_clip_seconds),
    ]
    if args.wide_manifest:
        app_args = [
            "--manifest",
            str(Path(args.wide_manifest).expanduser().resolve()),
            *common_args,
        ]
        if args.mute:
            app_args.append("--mute")
        return wide_monitor_app.main(app_args)

    app_args = [
        "--config",
        str(Path(args.config).expanduser().resolve()),
        *common_args,
    ]
    if not args.calibrated:
        app_args.append("--context-only")
    if args.mute:
        app_args.append("--mute")
    return monitor_app.main(app_args)


if __name__ == "__main__":
    raise SystemExit(main())
