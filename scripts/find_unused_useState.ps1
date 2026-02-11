$files = Get-ChildItem -Path frontend -Recurse -Filter *.tsx
$bad = @()

foreach ($f in $files) {
    $text = Get-Content -Raw $f.FullName
    
    # Check if useState is imported
    if ($text -match 'import\s+\{[^}]*\buseState\b') {
        # Count how many times useState is used
        $matches = ([regex]::Matches($text, '\buseState\b')).Count
        
        # If used only once (the import), it's unused
        if ($matches -le 1) {
            $bad += $f.FullName
        }
    }
}

Write-Host "Files with unused useState imports:"
Write-Host "===================================="
$bad | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "Total: $($bad.Count) files"
