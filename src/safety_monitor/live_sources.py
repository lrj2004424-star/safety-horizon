"""Explicit live-source selection / 明确选择窗口或 RTSP，不猜测输入。"""
import getpass
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit


def validate_config(config):
    if not isinstance(config, dict) or config.get('kind') not in ('rtsp', 'window'):
        raise ValueError('Source kind must be rtsp or window')
    if config['kind'] == 'rtsp':
        url = config.get('url', '')
        if not isinstance(url, str) or urlsplit(url).scheme not in ('rtsp', 'rtsps') or not urlsplit(url).hostname:
            raise ValueError('A valid rtsp:// or rtsps:// address is required')
    else:
        if not isinstance(config.get('window_title'), str) or not config['window_title'].strip():
            raise ValueError('An exact window title is required')
        if not config.get('process_name'):
            raise ValueError('Select a window with its process name')
    roi = config.get('roi', [0, 0, 1, 1])
    if (not isinstance(roi, list) or len(roi) != 4 or
        not all(isinstance(v, (int, float)) and math.isfinite(v) for v in roi) or
        not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1)):
        raise ValueError('ROI must be normalized x0,y0,x1,y1 within 0..1')
    config = dict(config)
    config['roi'] = roi
    return config


def load_source_config(path):
    try:
        return validate_config(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('Invalid source config; check kind, address/window and ROI (credentials hidden)') from None


class CroppedCamera:
    def __init__(self, device, roi):
        self.device, self.roi = device, roi
    def read_observation(self):
        from .camera_device import CameraRead
        read = self.device.read_observation()
        if read.ok and read.frame is not None:
            h, w = read.frame.shape[:2]
            a,b,c,d = self.roi
            frame = read.frame[int(b*h):int(d*h), int(a*w):int(c*w)].copy()
            if frame.size:
                return CameraRead(True, frame, read.captured_monotonic_s, read.sequence, read.reconnect_count, read.reason)
            return CameraRead(False, None, time.monotonic(), read.sequence, read.reconnect_count, 'empty_video_roi')
        return read
    def get(self, prop):
        return self.device.get(prop)
    def release(self):
        self.device.release()


def build_source(path, fps):
    config = load_source_config(path)
    if config['kind'] == 'rtsp':
        from .camera_device import CameraDevice
        return CroppedCamera(CameraDevice(config['url'], fps=fps, reconnect_attempts=1, strict_timeouts=True), config['roi'])
    from .windows_capture import WindowsWindowCapture
    return WindowsWindowCapture(config['window_title'], config['process_name'], config['roi'], fps=fps)


def configure(root):
    """User-facing selection; RTSP credentials never go in shell arguments."""
    root = Path(root)
    print('1 Window / 窗口   2 RTSP   3 Test video / 本地视频   4 Review UI / 审核界面')
    choice = input('Select 1-4: ').strip()
    argv = [sys.executable, str(root / 'horizon.py'), 'run']
    config = None
    if choice == '1' and os.name == 'nt':
        from .windows_capture import list_windows
        windows = list_windows()
        for i, win in enumerate(windows, 1):
            print(i, win['title'], '/', win['process'])
        index = int(input('Window number: ')) - 1
        if not 0 <= index < len(windows):
            raise ValueError('Invalid window number')
        win = windows[index]
        config = {'kind': 'window', 'window_title': win['title'], 'process_name': win['process']}
        print('Enter VIDEO area within the selected window client area, excluding controls.')
        config['roi'] = [float(x) for x in input('x0,y0,x1,y1 (0..1): ').split(',')]
    elif choice == '1' and sys.platform == 'darwin':
        pass  # Preserve the established macOS EZVIZ capture route.
    elif choice == '2':
        config = {'kind': 'rtsp', 'url': getpass.getpass('RTSP URL (hidden): ')}
    elif choice == '3':
        print('Place your authorized video under src/test_videos first.')
        argv += ['--test-video', input('Path: ').strip().strip('"')]
    elif choice == '4':
        argv += ['--no-vision']
    else:
        raise ValueError('Choose 1-4 on macOS or Windows')
    if config:
        config = validate_config(config)
        destination = root / 'src/runtime/source.private.json'
        destination.parent.mkdir(exist_ok=True)
        from .review_store import ReviewStore
        ReviewStore._write_json_atomic(destination, config)
        if os.name != 'nt':
            destination.chmod(0o600)
        print('Private source saved under src/runtime; do not share this folder.')
        argv += ['--source-config', str(destination)]
    print('Alerts are off by default. Ctrl+C stops the session.')
    return subprocess.call(argv, cwd=root)
