# build_exe.ps1
# ===============
# Rebuilds SectorRotationDashboard.exe (bundles sector_rotation_monitor.py,
# plot_rotation.py, generate_dashboard.py via run_pipeline.py).
#
# Run this after editing any of the scripts, to sync the exe.
#
# Usage:
#   .\build_exe.ps1
#
# Note: this file is kept ASCII-only on purpose. Windows PowerShell 5.1
# parses .ps1 files using the system codepage unless they have a UTF-8 BOM,
# so non-ASCII (e.g. Japanese) text here can corrupt parsing.

$ErrorActionPreference = "Stop"

$venvPyInstaller = Join-Path $PSScriptRoot "venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $venvPyInstaller)) {
    Write-Host "[ERROR] pyinstaller not found in venv. Run this first:"
    Write-Host "  .\venv\Scripts\python.exe -m pip install pyinstaller"
    exit 1
}

& $venvPyInstaller `
    --onefile `
    --name SectorRotationDashboard `
    --console `
    --noconfirm `
    --collect-all matplotlib `
    --collect-all curl_cffi `
    --collect-all yfinance `
    (Join-Path $PSScriptRoot "run_pipeline.py")

Write-Host ""
Write-Host "[DONE] dist\SectorRotationDashboard.exe has been rebuilt."
