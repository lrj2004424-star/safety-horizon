"""Download official models with digest checks / 官方模型下载与完整性校验。"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "pose_landmarker_full.task": ("pose_landmarker/pose_landmarker_full", "4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad"),
    "pose_landmarker_lite.task": ("pose_landmarker/pose_landmarker_lite", "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a"),
    "hand_landmarker.task": ("hand_landmarker/hand_landmarker", "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"),
}

def download():
    folder = ROOT / "src/models"
    folder.mkdir(parents=True, exist_ok=True)
    report = []
    for name, (model, digest) in MODELS.items():
        out = folder / name
        # Full model at /1 differs from the original validated /latest asset.
        # Keep original bytes pinned by digest; refuse future upstream changes.
        version = "latest" if name == "pose_landmarker_full.task" else "1"
        url = f"https://storage.googleapis.com/mediapipe-models/{model}/float16/{version}/{name}"
        if not out.exists():
            request = urllib.request.Request(url, headers={"User-Agent": "SafetyHorizon/0.1.0-rc.1"})
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read(50 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError(f"Model checksum mismatch / 模型校验失败: {name}")
            temp = out.with_suffix(".download")
            temp.write_bytes(data)
            os.replace(temp, out)
        if hashlib.sha256(out.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Existing model checksum mismatch: {name}; preserve it and check source.")
        report.append({"model": name, "source": url, "sha256": digest, "verified": True})
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-models", action="store_true", required=True)
    parser.parse_args()
    download()
