"""Verify both platform ZIPs and run regression from the current-platform extracted copy."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def verify(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert len(names) == len(set(names)), 'Duplicate archive members'
        for name in names:
            rel = PurePosixPath(name)
            assert not rel.is_absolute() and '..' not in rel.parts and '\\' not in name
            assert rel.parts[0] == 'SafetyHorizon'
            assert not {'runtime', 'annotations', '.venv', '.git', '.tools', '.cache'} & set(rel.parts)
        bundle.extractall(target)
    root = target / 'SafetyHorizon'
    checksums = (root/'SHA256SUMS').read_text(encoding='utf-8').splitlines()
    expected = set()
    for line in checksums:
        digest, name = line.split('  ', 1)
        expected.add(name)
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == digest, name
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    assert actual == expected | {'SHA256SUMS'}, 'Incomplete checksum coverage'
    assert (root/'horizon.py').is_file() and (root/'src/safety_monitor/review_store.py').is_file()
    print(f'{archive.name}: {len(expected)} file hashes PASS', flush=True)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, help='Validate existing published artifacts instead of rebuilding')
    parser.add_argument('--skip-tests', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='safety-release-') as tmp:
        tmp = Path(tmp)
        archives = args.archives or tmp/'archives'
        if args.archives is None:
            subprocess.run([sys.executable, str(ROOT/'scripts/package_release.py'), '--platforms', '--output', str(archives)], check=True)
        zips = sorted(archives.glob('*.zip'))
        assert len(zips) == 2, 'Expected exactly macOS and Windows archives'
        outer = {name: digest for digest, name in (line.split('  ',1) for line in (archives/'SHA256SUMS').read_text().splitlines())}
        assert set(outer) == {p.name for p in zips}
        selected = None
        for index, archive in enumerate(zips):
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == outer[archive.name]
            root = verify(archive, tmp/str(index))
            if ('Windows-x64' if os.name == 'nt' else 'macOS-arm64') in archive.name:
                selected = root
        assert selected is not None
        if not args.skip_tests:
            subprocess.run([sys.executable, str(selected/'horizon.py'), 'test'], cwd=selected, check=True, timeout=180)
        print('Distribution validation PASS', flush=True)


if __name__ == '__main__':
    main()
