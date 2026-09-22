"""Allowlisted source packaging / 白名单打包，不打包现场数据或环境。"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text().strip()
GENERATED = {"FILE_INDEX.md", "SHA256SUMS", "BUILD_MANIFEST.json"}
TOP_FILES = {"README.md", "LICENSE", "VERSION", "RELEASE_NOTES.md", "THIRD_PARTY_NOTICES.md",
    "requirements.in", "requirements.lock", "requirements-windows.lock", ".gitignore", ".python-version", "horizon.py", "web_portal.py",
    "01_install_macos.command", "02_run_standalone.command", "03_test.command", "SOURCE_PROVENANCE.json"}


def allowed(rel):
    s = rel.as_posix()
    if s in TOP_FILES or s in GENERATED:
        return True
    if "__pycache__" in rel.parts or rel.name.startswith("."):
        return False
    if rel.parts[0] in {"docs", "modules", "release_tests", "scripts", "ui", ".github", "platforms"}:
        return rel.suffix in {".md", ".py", ".html", ".css", ".js", ".yml", ".sh", ".ps1", ".cmd", ".command"}
    if rel.parts[0] != "src":
        return False
    if len(rel.parts) == 2:
        return rel.suffix in {".py", ".command"}
    folder = rel.parts[1]
    if folder in {"safety_monitor", "tests", "touchdesigner"}:
        return rel.suffix == ".py"
    if folder == "config":
        return rel.suffix == ".json" and not rel.name.endswith((".active.json", ".proposed.json"))
    if folder == "macos_window_capture":
        return rel.suffix in {".c", ".swift"}
    if folder == "hardware":
        return rel.suffix == ".ino"
    if folder in {"models", "test_videos"}:
        return rel.name == "README.md"
    if folder == "assets":
        return len(rel.parts) == 4 and rel.parts[2] == "sounds" and rel.suffix == ".wav"
    return False


def purpose(path):
    rel = path.relative_to(ROOT)
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(path.read_text()))
        except (SyntaxError, UnicodeError):
            doc = None
        if doc:
            return doc.splitlines()[0].replace("|", "/")[:160]
    categories = [("platforms/macos/", "macOS install/run/test guide / 苹果电脑入口"),
        ("platforms/windows/", "Windows install/run/test guide / Windows 电脑入口"),
        (".github/", "Cross-platform CI / 两平台自动化验证"),
        ("src/tests/", "Core regression / 核心模块测试"),
        ("release_tests/", "Release integration tests / 发布接口测试"),
        ("src/config/", "Example / research configuration; recalibrate / 示例配置需现场标定"),
        ("src/hardware/", "Arduino firmware / 板卡固件"),
        ("src/assets/", "Generated diagnostic sound; not default alert / 生成的旧诊断音效"),
        ("src/macos_window_capture/", "Native macOS capture helper source / 原生采集源码"),
        ("src/safety_monitor/", "Shared vision/review library / 共享视觉审核库"),
        ("ui/", "Standalone local review interface / 独立本地审核界面"),
        ("modules/", "Numbered module guide / 编号模块说明"),
        ("docs/", "Numbered deployment documentation / 部署文档")]
    return next((v for k,v in categories if rel.as_posix().startswith(k)), "Release source or metadata / 发布源码与元数据")


def build(output, components=False, platforms=False):
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and not p.is_symlink() and allowed(p.relative_to(ROOT)) and p.name not in GENERATED)
    for p in files:
        rel = p.relative_to(ROOT)
        if any(ord(c) > 127 for c in str(rel)):
            raise ValueError(f"Non-ASCII release filename: {rel}")
        if p.suffix in {".py", ".json", ".md", ".command", ".sh"}:
            content = p.read_text()
            if "/Users/" in content and p.name not in {"package_release.py", "test_portal.py"}:
                raise ValueError(f"Personal absolute path in {rel}")
    index = ["# File Index / 逐文件索引", "", "路径为发布根目录相对路径。编号模块导航见 modules；不更名原 Python 导入模块。", "", "| File | Purpose / 用途 |", "|---|---|"]
    index += [f"| `{p.relative_to(ROOT).as_posix()}` | {purpose(p)} |" for p in files]
    (ROOT / "FILE_INDEX.md").write_text("\n".join(index) + "\n")
    manifest = {"version": VERSION, "author": "Safety Horizon--Lrj", "license": "MIT", "contains_factory_data": False,
                "policy": "explicit allowlist; models/environments/runtime/annotations excluded", "files": [p.relative_to(ROOT).as_posix() for p in files]}
    (ROOT / "BUILD_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    files += [ROOT / "FILE_INDEX.md", ROOT / "BUILD_MANIFEST.json"]
    checksums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT).as_posix()}\n" for p in sorted(files))
    (ROOT / "SHA256SUMS").write_text(checksums)
    files.append(ROOT / "SHA256SUMS")
    names = [(f"SafetyHorizon-v{VERSION}-source.zip", None)]
    if platforms:
        names = [(f'SafetyHorizon-v{VERSION}-macOS-arm64.zip', ROOT/'platforms/macos'),
                 (f'SafetyHorizon-v{VERSION}-Windows-x64.zip', ROOT/'platforms/windows')]
    if components:
        names += [(f"SafetyHorizon-v{VERSION}-{d.name}.zip", d) for d in sorted((ROOT / "modules").iterdir()) if d.is_dir()]
    outputs = []
    for name, module in names:
        out = output / name
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in sorted(files):
                if module and platforms and p.name == 'SHA256SUMS':
                    continue
                z.write(p, "SafetyHorizon/" + p.relative_to(ROOT).as_posix())
            if module:
                if platforms:
                    intro = (module / 'README.md').read_text(encoding='utf-8').encode('utf-8')
                    z.writestr('SafetyHorizon/00_START_HERE.md', intro)
                    z.writestr('SafetyHorizon/SHA256SUMS', checksums +
                        hashlib.sha256(intro).hexdigest() + '  00_START_HERE.md\n')
                else:
                    z.writestr("SafetyHorizon/00_SELECTED_MODULE.md", (module / "README.md").read_text() +
                               "\n\n此模块包包含同版本共享核心以免缺依赖；只看本模块请从本文入口开始。\n")
        outputs.append((name, hashlib.sha256(out.read_bytes()).hexdigest()))
    (output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name,digest in outputs))
    print(json.dumps({"release_files": len(files), "archives": [name for name,_ in outputs]}, indent=2))

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=ROOT / "dist")
    p.add_argument("--components", action="store_true")
    p.add_argument('--platforms', action='store_true')
    a = p.parse_args()
    build(a.output, a.components, a.platforms)
