#!/usr/bin/env python3
"""List local camera indices that can return a frame without saving images."""

from __future__ import annotations

import argparse
import json
import sys

from safety_monitor.camera_device import probe_camera_indices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-index", type=int, default=6)
    args = parser.parse_args()
    devices = probe_camera_indices(args.max_index)
    print(json.dumps(devices, ensure_ascii=False, indent=2))
    if not devices:
        hint = (
            "No camera returned a frame. On macOS, enable Camera access for the "
            "current terminal/Codex/Python in System Settings > Privacy & Security > Camera."
        )
        print(hint, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
