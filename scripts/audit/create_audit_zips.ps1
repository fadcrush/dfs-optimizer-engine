# create_audit_zips.ps1
# Creates audit-friendly ZIP bundles using the *correct* paths for your repo.
# Run from repo root: F:\Dev\N_B_A_and_N_F_L

$ErrorActionPreference = "Stop"

$ROOT = (Get-Location).Path
$OUT_DIR = Join-Path $ROOT "audit_zips"

if (!(Test-Path $OUT_DIR)) {
  New-Item -ItemType Directory -Path $OUT_DIR | Out-Null
}

function Resolve-Files([string[]]$relPaths) {
  $resolved = @()
  foreach ($p in $relPaths) {
    $full = Join-Path $ROOT $p
    if (Test-Path $full) {
      $resolved += $full
    } else {
      Write-Warning "Missing file: $p"
    }
  }
  return $resolved
}

function New-AuditZip([string]$zipName, [string[]]$relPaths) {
  $zipPath = Join-Path $OUT_DIR $zipName
  if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

  $files = Resolve-Files $relPaths
  if ($files.Count -eq 0) {
    Write-Warning "No valid files found for $zipName"
    return
  }

  Compress-Archive -Path $files -DestinationPath $zipPath -Force
  Write-Host "Created $zipPath"
}

# -----------------------------
# BUNDLE 1: Analysis (root-level)
# -----------------------------
New-AuditZip "analysis_core.zip" @(
  "analysis\nba\player_pool.py",
  "backend\services\simulation_engine.py"
)

# -----------------------------
# BUNDLE 2: Backend (pipeline + optimizer wiring)
# -----------------------------
New-AuditZip "backend_core.zip" @(
  "backend\main.py",
  "backend\services\optimizer_service.py",
  "backend\signals\sources\nba\beat_writer_live.py",
  "backend\routers\signals.py",
  "backend\api\signals.py"
)

# -----------------------------
# BUNDLE 3: Frontend (signals + intel + groups UI)
# -----------------------------
New-AuditZip "frontend_features.zip" @(
  "frontend\package.json",
  "frontend\src\app\page.tsx",
  "frontend\src\components\signals\SignalsDashboardPage.tsx",
  "frontend\src\components\signals\SignalsStatusPanel.tsx",
  "frontend\src\components\intel\IntelFeed.tsx",
  "frontend\src\components\groups\PlayerGroups.tsx"
)

Write-Host "`n✅ Audit ZIP creation complete."
Write-Host "Upload files from: $OUT_DIR"
