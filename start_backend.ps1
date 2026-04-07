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

# Check for a process already using port 8000 — auto-kill if possible
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pid8000 = $existing.OwningProcess | Select-Object -First 1
    $proc = Get-Process -Id $pid8000 -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Killing existing process on port 8000 (PID $pid8000)..."
        Stop-Process -Id $pid8000 -Force
        Start-Sleep -Seconds 2
    } else {
        # Zombie socket — process is gone but Windows hasn't released the port yet.
        # Wait briefly and proceed; uvicorn will surface a real error if port is truly blocked.
        Write-Warning "Port 8000 shows PID $pid8000 but that process no longer exists (zombie socket). Waiting 3s..."
        Start-Sleep -Seconds 3
    }
}

Set-Location "$PSScriptRoot"
Write-Host "Starting backend with .venv Python..."
& $venvPy -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
