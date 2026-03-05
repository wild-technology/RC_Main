# Session start hook for RC_Main Claude Code Agent sessions (Windows)
# Runs unit tests and checks RC availability

Write-Host "=== RC_Main Session Start ===" -ForegroundColor Cyan
Write-Host "Running unit test suite..."

python -m pytest tests/ -v -m "not windows_only" --tb=short -q 2>&1 | Select-Object -Last 5

if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Unit tests are failing. Review test output before proceeding." -ForegroundColor Yellow
} else {
    Write-Host "All unit tests passed." -ForegroundColor Green
}

# Check for RealityScan
$rcPaths = @(
    "C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe",
    "C:\Program Files\Capturing Reality\RealityScan 2.0\RealityScan.exe",
    "C:\Program Files\Capturing Reality\RealityScan\RealityScan.exe"
)

$rcFound = $false
foreach ($p in $rcPaths) {
    if (Test-Path $p) {
        Write-Host "RealityScan found: $p" -ForegroundColor Green
        $rcFound = $true
        break
    }
}

if ($env:RC_EXECUTABLE -and (Test-Path $env:RC_EXECUTABLE)) {
    Write-Host "RealityScan (env): $env:RC_EXECUTABLE" -ForegroundColor Green
    $rcFound = $true
}

if (-not $rcFound) {
    Write-Host "WARNING: RealityScan not found. Set RC_EXECUTABLE env var." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Environment:" -ForegroundColor Cyan
python --version
Write-Host "Working directory: $(Get-Location)"
Write-Host "Git branch: $(git branch --show-current 2>$null)"
Write-Host "===========================" -ForegroundColor Cyan
