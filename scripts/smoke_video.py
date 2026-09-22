"""Actual model + local-video pipeline with synthetic non-person frames."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT/'src'
folder = SRC/'test_videos'
folder.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='synthetic-', dir=folder) as tmp:
    tmp = Path(tmp)
    video = tmp/'synthetic.avi'
    writer = cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(1280,720))
    assert writer.isOpened()
    rng = np.random.default_rng(101)
    for i in range(12):
        writer.write(rng.integers(35,220,(720,1280,3),dtype=np.uint8))
    writer.release()
    mode = tmp/'mode.json'
    mode.write_text(json.dumps({'mode':'TEST','test_video':str(video),'physical_actuator_allowed':False}))
    command = [sys.executable,'ezviz_multistation_fatigue_monitor.py','--headless','--max-frames','6',
        '--fps','10','--window-crop','full','--manifest',str(SRC/'config/ezviz-fatigue-single-upper.json'),
        '--input-mode-json',str(mode),'--captures-dir',str(tmp/'captures'),
        '--status-json',str(tmp/'status.json'),'--dashboard-output',str(tmp/'dashboard.jpg')]
    subprocess.run(command,cwd=SRC,check=True,timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    status = json.loads((tmp/'status.json').read_text(encoding='utf-8'))
    assert status['input_mode']=='TEST'
    assert (tmp/'dashboard.jpg').is_file()
    print('Synthetic file -> actual pose model -> TEST dashboard: PASS (not accuracy evidence)')
