# make_audit_zip.ps1
# Creates a clean, high-signal zip for architecture analysis (no venv/node_modules/.next)

$ErrorActionPreference = "Stop"

$repoRoot = (Get-Location).Path
$staging = Join-Path $repoRoot "_audit_bundle"
$zipPath = Join-Path $repoRoot "dfs_audit_bundle.zip"

# Clean staging
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null

function Copy-IfExists($relativePath) {
  $src = Join-Path $repoRoot $relativePath
  if (Test-Path $src) {
    $dst = Join-Path $staging $relativePath
    $dstDir = Split-Path $dst -Parent
    if (!(Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
    Copy-Item $src $dst -Recurse -Force
  }
}

# ---- Required / high signal ----
Copy-IfExists "RootTree.txt"
Copy-IfExists "README.md"
Copy-IfExists "docker-compose.yml"
Copy-IfExists "requirements.txt"
Copy-IfExists "pyproject.toml"
Copy-IfExists ".env.example"

# Backend (adjust if your backend folder differs)
Copy-IfExists "backend\main.py"
Copy-IfExists "backend\routers"
Copy-IfExists "backend\services"
Copy-IfExists "backend\models"
Copy-IfExists "backend\database"
Copy-IfExists "backend\db"
Copy-IfExists "backend\core"
Copy-IfExists "backend\orchestration"
Copy-IfExists "backend\entrypoints"
Copy-IfExists "backend\signals"

# Domain logic (analysis)
Copy-IfExists "analysis\nba"
Copy-IfExists "analysis\nfl"

# Frontend (source only)
Copy-IfExists "frontend\package.json"
Copy-IfExists "frontend\package-lock.json"
Copy-IfExists "frontend\pnpm-lock.yaml"
Copy-IfExists "frontend\yarn.lock"
Copy-IfExists "frontend\next.config.js"
Copy-IfExists "frontend\next.config.mjs"
Copy-IfExists "frontend\tsconfig.json"
Copy-IfExists "frontend\src"
Copy-IfExists "frontend\public"

# Optional useful folders
Copy-IfExists "tests"
Copy-IfExists "scripts"
Copy-IfExists "docs"
Copy-IfExists "config"

# ---- Delete junk inside staging just in case ----
$junks = @(
  ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".next", "dist", "build", "coverage"
)

Get-ChildItem -Path $staging -Recurse -Force -ErrorAction SilentlyContinue |
  Where-Object { $junks -contains $_.Name } |
  ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

Get-ChildItem -Path $staging -Recurse -Include *.pyc -ErrorAction SilentlyContinue |
  ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

# ---- Create zip ----
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zipPath -Force

Write-Host "✅ Created: $zipPath"
Write-Host "Staging folder: $staging"
