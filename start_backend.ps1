# DFS Edge Pro — Start Backend
# Runs uvicorn using the .venv Python so all dependencies (psycopg2, etc.) are available.
# Usage: .\start_backend.ps1

$venvPy = "$PSScriptRoot\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Error "Virtual environment not found at $venvPy"
    Write-Host "Create it with:  python -m venv .venv"
    Write-Host "Then install:    .venv\Scripts\pip install -r backend\requirements.txt"
    exit 1
}

# Check for a process already using port 8000
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pid8000 = $existing.OwningProcess | Select-Object -First 1
    Write-Warning "Port 8000 is already in use by PID $pid8000. Kill it first:"
    Write-Host "  Stop-Process -Id $pid8000 -Force"
    exit 1
}

Set-Location "$PSScriptRoot"
Write-Host "Starting backend with .venv Python..."
& $venvPy -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
