# Windows x64 guided installer / Windows 安装入口 (run from extracted folder).
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'This package targets Windows 10/11 x64.'
}
$env:PYTHONUTF8 = '1'
$env:UV_CACHE_DIR = Join-Path $PWD '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.tools/python'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$uv = Join-Path $PWD '.tools/uv.exe'
if (-not (Test-Path $uv)) {
    $existing = Get-Command uv -ErrorAction SilentlyContinue
    if ($existing) { $uv = $existing.Source }
    else {
        $answer = Read-Host 'Download official uv + Python 3.12 into this project? [y/N]'
        if ($answer -ne 'y') { exit 1 }
        $env:UV_INSTALL_DIR = Join-Path $PWD '.tools'
        $env:UV_NO_MODIFY_PATH = '1'
        $installer = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName() + '.ps1')
        try {
            Invoke-WebRequest 'https://astral.sh/uv/install.ps1' -OutFile $installer
            & $installer
            if (-not (Test-Path $uv)) { throw 'uv installation did not complete.' }
        } finally { Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue }
    }
}
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    & $uv venv --python 3.12 --seed .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
& '.venv/Scripts/python.exe' -c 'import sys; assert sys.version_info[:2] == (3,12)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
& $uv pip sync --python .venv/Scripts/python.exe --require-hashes requirements-windows.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& '.venv/Scripts/python.exe' -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency conflict.' }
& '.venv/Scripts/python.exe' scripts/setup_assets.py --download-models
if ($LASTEXITCODE -ne 0) { throw 'Model download/checksum failed.' }
& '.venv/Scripts/python.exe' horizon.py doctor
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
Write-Host 'Installed. Run platforms/windows/02_run.cmd next.'
