Param()

$ErrorActionPreference = 'Stop'

Write-Host '[backend-smoke] python version'
python --version

Write-Host '[backend-smoke] compile-check backend entrypoints'
python -m py_compile backend/main.py
python -m py_compile backend/cli.py

Write-Host '[backend-smoke] OK'
