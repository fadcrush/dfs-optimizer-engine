param(
    [ValidateSet("Plan", "Execute")]
    [string]$Mode = "Plan",

    [switch]$SkipTests,

    [switch]$AutoApprove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Text)
    Write-Host ("  - " + $Text) -ForegroundColor DarkGray
}

function Invoke-Step {
    param(
        [string]$Description,
        [scriptblock]$Action
    )

    if ($Mode -eq "Plan") {
        Write-Host ("[PLAN] " + $Description) -ForegroundColor Yellow
        return
    }

    Write-Host ("[EXEC] " + $Description) -ForegroundColor Green
    & $Action
}

function Confirm-Batch {
    param(
        [string]$Title,
        [string]$Prompt = "Proceed",
        [string]$RequiredPhrase = ""
    )

    Write-Host ""
    Write-Host ("Batch boundary: " + $Title) -ForegroundColor Magenta

    if ($AutoApprove) {
        Write-Host "AutoApprove enabled. Continuing." -ForegroundColor DarkYellow
        return $true
    }

    if ([string]::IsNullOrWhiteSpace($RequiredPhrase)) {
        $answer = Read-Host ($Prompt + "? Type Y to continue")
        return ($answer -eq "Y" -or $answer -eq "y")
    }

    $typed = Read-Host ($Prompt + ". Type exactly: " + $RequiredPhrase)
    return ($typed -eq $RequiredPhrase)
}

function New-DirectoryIfMissing {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Move-ItemIfExists {
    param(
        [string]$Source,
        [string]$Destination
    )

    $items = Get-ChildItem -Path $Source -Force -ErrorAction SilentlyContinue
    if (-not $items) {
        Write-Step ("No matches for " + $Source)
        return
    }

    New-DirectoryIfMissing -Path $Destination

    foreach ($item in $items) {
        $target = Join-Path $Destination $item.Name
        if (Test-Path -LiteralPath $target) {
            Write-Step ("Target exists, skipping: " + $target)
            continue
        }
        Move-Item -LiteralPath $item.FullName -Destination $Destination -Force
        Write-Step ("Moved: " + $item.FullName + " -> " + $Destination)
    }
}

function Move-PathIfExists {
    param(
        [string]$Path,
        [string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Step ("Not found, skipping: " + $Path)
        return
    }

    New-DirectoryIfMissing -Path $Destination

    $name = Split-Path -Path $Path -Leaf
    $target = Join-Path $Destination $name
    if (Test-Path -LiteralPath $target) {
        Write-Step ("Target exists, skipping: " + $target)
        return
    }

    Move-Item -LiteralPath $Path -Destination $Destination -Force
    Write-Step ("Moved: " + $Path + " -> " + $Destination)
}

function Replace-InFileIfExists {
    param(
        [string]$Path,
        [string]$OldText,
        [string]$NewText
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Step ("File missing, skip rewrite: " + $Path)
        return
    }

    $content = Get-Content -LiteralPath $Path -Raw -Encoding utf8
    if ($content -notlike ("*" + $OldText + "*")) {
        Write-Step ("Rewrite not needed: " + $Path)
        return
    }

    $updated = $content.Replace($OldText, $NewText)
    Set-Content -LiteralPath $Path -Value $updated -Encoding utf8
    Write-Step ("Rewrote references in: " + $Path)
}

function Remove-ItemIfExists {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Step ("Removed: " + $Path)
    }
    else {
        Write-Step ("Not found, skipping remove: " + $Path)
    }
}

function Invoke-ValidationSuite {
    Write-Section "Validation"

    if ($SkipTests) {
        Write-Host "Skipping validation commands due to -SkipTests." -ForegroundColor Yellow
        return
    }

    Invoke-Step -Description "Run backend tests" -Action {
        pytest backend/tests -q
    }

    Invoke-Step -Description "Run analysis/core tests" -Action {
        pytest tests -q
    }

    Invoke-Step -Description "Run frontend lint" -Action {
        npm --prefix frontend run lint
    }
}

function Assert-RepoRoot {
    if (-not (Test-Path -LiteralPath ".git")) {
        throw "Run this script from the repository root. .git not found."
    }
}

Write-Section "Repo Cleanup Safe Script"
Write-Host ("Mode: " + $Mode) -ForegroundColor White
Write-Host "This script performs staged cleanup with safety prompts at each batch boundary." -ForegroundColor White

Assert-RepoRoot

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$branchName = "chore/repo-cleanup-phase1-" + $timestamp

$archiveRoot = "archive_staging"
$archiveFixScripts = Join-Path $archiveRoot "root_fix_scripts"
$archiveUtilities = Join-Path $archiveRoot "root_utilities"
$archiveLegacy = Join-Path $archiveRoot "legacy_modules"
$archiveTrees = Join-Path $archiveRoot "tree_snapshots"

# Batch 0: Safety snapshot
if (Confirm-Batch -Title "Batch 0: Safety Snapshot" -Prompt "Start Batch 0") {
    Write-Section "Batch 0: Safety Snapshot"

    Invoke-Step -Description "Ensure logs directory exists" -Action {
        New-DirectoryIfMissing -Path "logs"
    }

    Invoke-Step -Description "Create safety branch" -Action {
        git checkout -b $branchName
    }

    Invoke-Step -Description "Capture current git status" -Action {
        git status --short | Out-File -FilePath "logs/cleanup_status_before.txt" -Encoding utf8
    }

    Invoke-Step -Description "Capture current commit" -Action {
        git rev-parse --short HEAD | Out-File -FilePath "logs/cleanup_baseline_commit.txt" -Encoding utf8
    }

    Invoke-Step -Description "Capture tracked file list" -Action {
        git ls-files | Out-File -FilePath "logs/cleanup_tracked_files_before.txt" -Encoding utf8
    }
}

# Batch 1: Non-destructive archive staging
if (Confirm-Batch -Title "Batch 1: Archive Staging (No Deletes)" -Prompt "Run Batch 1") {
    Write-Section "Batch 1: Archive Staging"

    Invoke-Step -Description "Create archive staging folders" -Action {
        New-DirectoryIfMissing -Path $archiveFixScripts
        New-DirectoryIfMissing -Path $archiveUtilities
        New-DirectoryIfMissing -Path $archiveLegacy
        New-DirectoryIfMissing -Path $archiveTrees
    }

    Invoke-Step -Description "Move root fix and reformat scripts" -Action {
        Move-ItemIfExists -Source "fix*.py" -Destination $archiveFixScripts
        Move-ItemIfExists -Source "fix*.ps1" -Destination $archiveFixScripts
        Move-ItemIfExists -Source "reformat*.js" -Destination $archiveFixScripts
    }

    Invoke-Step -Description "Move root utility scripts" -Action {
        Move-ItemIfExists -Source "audit_styles.py" -Destination $archiveUtilities
        Move-ItemIfExists -Source "find_data_sources.py" -Destination $archiveUtilities
        Move-ItemIfExists -Source "inspect_misses.py" -Destination $archiveUtilities
        Move-ItemIfExists -Source "create_claude_export.ps1" -Destination $archiveUtilities
    }

    Invoke-Step -Description "Move tree snapshot text files" -Action {
        Move-ItemIfExists -Source "full_tree.txt" -Destination $archiveTrees
        Move-ItemIfExists -Source "project_tree.txt" -Destination $archiveTrees
        Move-ItemIfExists -Source "clean_tree.txt" -Destination $archiveTrees
        Move-ItemIfExists -Source "treeunti.txt" -Destination $archiveTrees
    }

    Invoke-Step -Description "Stage legacy module directories" -Action {
        Move-PathIfExists -Path "dashboard" -Destination $archiveLegacy
        Move-PathIfExists -Path "nba_news" -Destination $archiveLegacy
        Move-PathIfExists -Path "optimizer" -Destination $archiveLegacy
    }

    Invoke-ValidationSuite
}

# Batch 2: Generated artifact cleanup
if (Confirm-Batch -Title "Batch 2: Remove Generated Artifacts" -Prompt "Run Batch 2") {
    Write-Section "Batch 2: Generated Artifact Cleanup"

    Invoke-Step -Description "Remove all __pycache__ directories" -Action {
        Get-ChildItem -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    }

    Invoke-Step -Description "Remove .pytest_cache" -Action {
        Remove-ItemIfExists -Path ".pytest_cache"
    }

    Invoke-Step -Description "Remove *.pyc and *.pyo files" -Action {
        Get-ChildItem -File -Recurse -Include "*.pyc", "*.pyo" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }

    Invoke-ValidationSuite
}

# Batch 3: Script taxonomy consolidation
if (Confirm-Batch -Title "Batch 3: Reorganize scripts directory" -Prompt "Run Batch 3") {
    Write-Section "Batch 3: Script Taxonomy Consolidation"

    Invoke-Step -Description "Create script taxonomy folders" -Action {
        New-DirectoryIfMissing -Path "scripts/jobs"
        New-DirectoryIfMissing -Path "scripts/dev"
        New-DirectoryIfMissing -Path "scripts/audit"
        New-DirectoryIfMissing -Path "scripts/tmp"
    }

    Invoke-Step -Description "Move operational jobs into scripts/jobs" -Action {
        Move-ItemIfExists -Source "scripts/fetch_nba_injuries.py" -Destination "scripts/jobs"
        Move-ItemIfExists -Source "scripts/ingest_game_logs.py" -Destination "scripts/jobs"
        Move-ItemIfExists -Source "scripts/import_contest_results.py" -Destination "scripts/jobs"
        Move-ItemIfExists -Source "scripts/run_dfs_pipeline.py" -Destination "scripts/jobs"
    }

    Invoke-Step -Description "Rewrite known imports/paths to scripts/jobs" -Action {
        Replace-InFileIfExists -Path "workers/schedulers/daily.py" -OldText "from scripts.fetch_nba_injuries import ensure_current, DB_PATH" -NewText "from scripts.jobs.fetch_nba_injuries import ensure_current, DB_PATH"
        Replace-InFileIfExists -Path "workers/schedulers/daily.py" -OldText "from scripts.import_contest_results import scan_and_import" -NewText "from scripts.jobs.import_contest_results import scan_and_import"
        Replace-InFileIfExists -Path "workers/schedulers/daily.py" -OldText "_ROOT / \"scripts\" / \"ingest_game_logs.py\"" -NewText "_ROOT / \"scripts\" / \"jobs\" / \"ingest_game_logs.py\""

        Replace-InFileIfExists -Path "analysis/core/orchestrator.py" -OldText "_scripts = _Path(__file__).resolve().parent.parent.parent / \"scripts\"" -NewText "_scripts = _Path(__file__).resolve().parent.parent.parent / \"scripts\" / \"jobs\""
    }

    Invoke-Step -Description "Move temporary probe scripts into scripts/tmp" -Action {
        Move-ItemIfExists -Source "scripts/_tmp_*.py" -Destination "scripts/tmp"
    }

    Invoke-Step -Description "Move audit scripts into scripts/audit" -Action {
        Move-ItemIfExists -Source "scripts/audit_*.py" -Destination "scripts/audit"
        Move-ItemIfExists -Source "scripts/create_audit*.ps1" -Destination "scripts/audit"
        Move-ItemIfExists -Source "scripts/make_audit_zip.ps1" -Destination "scripts/audit"
    }

    Invoke-Step -Description "Show paths that may need import updates" -Action {
        if (Get-Command rg -ErrorAction SilentlyContinue) {
            rg "fetch_nba_injuries|ingest_game_logs|import_contest_results|run_dfs_pipeline" workers backend analysis scripts -g "*.py"
        }
        else {
            Write-Host "ripgrep not found. Skipping reference scan." -ForegroundColor Yellow
        }
    }

    Invoke-ValidationSuite
}

# Batch 4: Optional hard delete from archive staging
if (Confirm-Batch -Title "Batch 4: Hard Deletes" -Prompt "Run Batch 4 (destructive)" -RequiredPhrase "DELETE") {
    Write-Section "Batch 4: Hard Delete Candidates"

    Invoke-Step -Description "Delete staged legacy module directories" -Action {
        Remove-ItemIfExists -Path (Join-Path $archiveLegacy "dashboard")
        Remove-ItemIfExists -Path (Join-Path $archiveLegacy "nba_news")
        Remove-ItemIfExists -Path (Join-Path $archiveLegacy "optimizer")
    }

    Invoke-Step -Description "Delete staged root fix scripts" -Action {
        if (Test-Path -LiteralPath $archiveFixScripts) {
            Get-ChildItem -Path $archiveFixScripts -Force -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
        }
    }

    Invoke-ValidationSuite
}

Write-Section "Cleanup Run Complete"
Write-Host "Recommended next commands:" -ForegroundColor White
Write-Host "  git status --short" -ForegroundColor DarkGray
Write-Host "  git diff --name-status" -ForegroundColor DarkGray
Write-Host "  pytest backend/tests -q" -ForegroundColor DarkGray
Write-Host "  pytest tests -q" -ForegroundColor DarkGray
Write-Host "  npm --prefix frontend run build" -ForegroundColor DarkGray
