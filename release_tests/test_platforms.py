"""Cross-platform checks / 平台互斥、采集路由、退出与隔离测试。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from safety_monitor.platform_runtime import lock_file, unlock_file, OwnedJob
from safety_monitor.live_sources import validate_config, load_source_config, CroppedCamera, build_source
from safety_monitor.windows_capture import overlaps


class PlatformTests(unittest.TestCase):
    def test_lock_excludes_other_process_then_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lock'
            code = ('import sys; from safety_monitor.platform_runtime import lock_file; '
                    'f=open(sys.argv[1],"a+b"); lock_file(f,blocking=False)')
            with path.open('a+b') as handle:
                lock_file(handle)
                blocked = subprocess.run([sys.executable, '-c', code, str(path)], cwd=ROOT/'src', capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
                unlock_file(handle)
                allowed = subprocess.run([sys.executable, '-c', code, str(path)], cwd=ROOT/'src', capture_output=True)
                self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_source_validation_and_secret_redaction(self):
        self.assertEqual(validate_config({'kind': 'rtsp', 'url': 'rtsp://user:fake@example.invalid/video'})['roi'], [0,0,1,1])
        for roi in ([0,0,0,1], [-1,0,1,1], [0,0,float('nan'),1], [0,0,2,1]):
            with self.assertRaises(ValueError):
                validate_config({'kind': 'window', 'window_title': 'test', 'process_name':'test.exe', 'roi':roi})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source.json'
            path.write_text('{"kind":"rtsp","url":"https://secret@example.invalid"}')
            with self.assertRaises(ValueError) as error:
                load_source_config(path)
            self.assertNotIn('secret', str(error.exception))

    def test_crop_preserves_metadata_and_pixel_bounds(self):
        import numpy as np
        from safety_monitor.camera_device import CameraRead
        from unittest.mock import Mock
        frame = np.arange(10*20*3, dtype=np.uint8).reshape(10,20,3)
        device = Mock()
        device.read_observation.return_value = CameraRead(True,frame,12.0,3,1,'ok')
        result = CroppedCamera(device,[.25,.2,.75,.8]).read_observation()
        np.testing.assert_array_equal(result.frame,frame[2:8,5:15])
        self.assertEqual((result.sequence,result.reconnect_count,result.captured_monotonic_s),(3,1,12.0))

    def test_rtsp_uses_bounded_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'source.json'
            path.write_text(json.dumps({'kind':'rtsp','url':'rtsp://example.invalid/camera'}))
            with patch('safety_monitor.camera_device.CameraDevice') as device:
                build_source(path,20)
                self.assertTrue(device.call_args.kwargs['strict_timeouts'])

    def test_window_overlap_geometry(self):
        self.assertTrue(overlaps((0,0,100,100),(90,90,110,110)))
        self.assertFalse(overlaps((0,0,100,100),(100,0,120,100)))

    @unittest.skipUnless(os.name == 'nt', 'Windows job object')
    def test_windows_job_terminates_owned_worker(self):
        job = OwnedJob()
        p = subprocess.Popen([sys.executable,'-c','import sys,time;sys.stdin.readline();time.sleep(60)'], stdin=subprocess.PIPE)
        try:
            job.attach(p)
            p.stdin.write(b'GO\n'); p.stdin.close()
            job.close()
            p.wait(timeout=5)
            self.assertIsNotNone(p.returncode)
        finally:
            job.close()
            if p.poll() is None:
                p.kill(); p.wait()

    def test_standalone_no_vision_start_and_stop(self):
        # Isolated copy: never mutate a user's live review queue.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)/'project'
            shutil.copytree(ROOT,project,ignore=shutil.ignore_patterns('.git','.cache','.venv','runtime','annotations','artifacts','__pycache__','*.task'))
            options = {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {}
            with (Path(tmp)/'run.log').open('wb') as log:
                p = subprocess.Popen([sys.executable,str(project/'horizon.py'),'run','--no-vision','--no-browser','--port','0'],stdout=log,stderr=log,**options)
                state = project/'src/runtime/safety-officer-review-state.json'
                try:
                    deadline=time.monotonic()+15
                    while time.monotonic()<deadline and not state.exists() and p.poll() is None:
                        time.sleep(.1)
                    self.assertIsNone(p.poll(),(Path(tmp)/'run.log').read_text(errors='replace'))
                    self.assertTrue(state.exists())
                    snapshot=json.loads(state.read_text(encoding='utf-8'))
                    self.assertEqual(snapshot['service_state'],'RUNNING')
                    import signal
                    p.send_signal(signal.CTRL_BREAK_EVENT if os.name=='nt' else signal.SIGTERM)
                    p.wait(timeout=15)
                    self.assertEqual(p.returncode,0)
                    self.assertEqual(json.loads((project/'src/runtime/review-actuator.json').read_text())['action'],'SILENT')
                finally:
                    if p.poll() is None:
                        p.kill();p.wait()
