# ===============================
# DFS Edge Pro – Audit Zip Script (Fixed)
# ===============================

Set-Location -Path $PSScriptRoot

$exclude = @(
    ".git",
    ".venv",
    "backend\.venv",
    "frontend\node_modules",
    "frontend\.next",
    "__pycache__",
    ".pytest_cache",
    "uploads"
)

$zipPath = "..\N_B_A_and_N_F_L_audit.zip"

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Write-Host "Creating audit zip..." -ForegroundColor Cyan

$items = Get-ChildItem -Force | Where-Object {
    $name = $_.Name
    -not ($exclude | Where-Object { $name -like $_ })
}

Compress-Archive `
    -Path $items `
    -DestinationPath $zipPath `
    -Force

Write-Host "✅ Audit zip created successfully at:" -ForegroundColor Green
Write-Host "   $zipPath"
