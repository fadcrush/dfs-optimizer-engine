Param()

$ErrorActionPreference = 'Stop'

Write-Host '[frontend-smoke] node/npm versions'
node --version
npm --version

Write-Host '[frontend-smoke] package script presence check'
python -c "import json, pathlib; p=pathlib.Path('frontend/package.json'); d=json.loads(p.read_text(encoding='utf-8')); s=d.get('scripts', {}); assert 'dev' in s; assert 'build' in s; assert 'lint' in s; print('OK: required frontend scripts present')"

Write-Host '[frontend-smoke] OK'
