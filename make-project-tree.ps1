param(
    [string]$Root = "F:\Dev\N_B_A_and_N_F_L"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExcludeDirs = @(
    ".git",".github",".vscode",".idea",
    ".venv","venv","env",
    "__pycache__",".pytest_cache",".mypy_cache",
    "node_modules","dist","build","out",
    "coverage","htmlcov",
    "logs","tmp","temp","snapshots",
    "artifacts"
)

$ExcludePatterns = @(
    "*.pyc","*.pyo","*.pyd",
    "*.dll","*.exe","*.so",
    "*.zip","*.tar","*.gz","*.7z",
    "*.png","*.jpg","*.jpeg","*.gif",
    "*.mp4","*.mp3",
    "*.parquet","*.feather",
    "*.db","*.sqlite*",
    "*.log","*.coverage"
)

$CriticalFiles = @(
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    "main.py",
    "app.py",
    "index.ts",
    "index.js"
)

$IncludeExtensions = @(
    ".py",".ts",".tsx",".js",".jsx",
    ".json",".yml",".yaml",".toml",
    ".md",".txt",".sql",
    ".ps1",".sh",".bat"
)

function Is-ExcludedDir {
    param([string]$Path)

    foreach ($dir in $ExcludeDirs) {
        if ($Path -like "*$dir*") {
            return $true
        }
    }
    return $false
}

function Is-ExcludedFile {
    param([System.IO.FileInfo]$File)

    foreach ($pattern in $ExcludePatterns) {
        if ($File.Name -like $pattern) {
            return $true
        }
    }

    if ($File.Length -gt 5MB -and -not ($CriticalFiles -contains $File.Name)) {
        return $true
    }

    return $false
}

function Is-RelevantFile {
    param([System.IO.FileInfo]$File)

    if (Is-ExcludedFile $File) {
        return $false
    }

    if ($CriticalFiles -contains $File.Name) {
        return $true
    }

    if ($IncludeExtensions -contains $File.Extension.ToLower()) {
        return $true
    }

    return $false
}

$Tree = New-Object System.Collections.Generic.List[string]

function Build-Tree {
    param(
        [string]$Path,
        [string]$Prefix = ""
    )

    $items = @(
        Get-ChildItem -LiteralPath $Path -Force |
            Where-Object {
                if ($_.PSIsContainer) {
                    -not (Is-ExcludedDir $_.FullName)
                }
                else {
                    Is-RelevantFile $_
                }
            } |
            Sort-Object -Property @{Expression = { -not $_.PSIsContainer }}, @{Expression = { $_.Name.ToLower() }}
    )

    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        $isLast = ($i -eq ($items.Count - 1))

        $branch = if ($isLast) { "\-- " } else { "+-- " }
        $Tree.Add("$Prefix$branch$($item.Name)") | Out-Null

        if ($item.PSIsContainer) {
            $newPrefix = if ($isLast) { "$Prefix    " } else { "$Prefix|   " }
            Build-Tree -Path $item.FullName -Prefix $newPrefix
        }
    }
}

function Get-CodeIndex {
    Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
        Where-Object {
            -not (Is-ExcludedDir $_.DirectoryName) -and (Is-RelevantFile $_)
        } |
        Sort-Object FullName |
        ForEach-Object {
            $_.FullName.Replace($Root, ".")
        }
}

function Get-PriorityFiles {
    Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
        Where-Object {
            -not (Is-ExcludedDir $_.DirectoryName) -and ($CriticalFiles -contains $_.Name)
        } |
        Sort-Object FullName |
        ForEach-Object {
            $_.FullName.Replace($Root, ".")
        }
}

if (-not (Test-Path -LiteralPath $Root)) {
    throw "Root path does not exist: $Root"
}

Write-Host "Starting project audit..."

$Tree.Add((Split-Path $Root -Leaf)) | Out-Null
Build-Tree -Path $Root

$Tree | Set-Content -Path "project_tree.txt" -Encoding UTF8
Get-CodeIndex | Set-Content -Path "code_index.txt" -Encoding UTF8
Get-PriorityFiles | Set-Content -Path "priority_files.txt" -Encoding UTF8

Write-Host "Audit complete."
Write-Host "Created:"
Write-Host " - project_tree.txt"
Write-Host " - code_index.txt"
Write-Host " - priority_files.txt"