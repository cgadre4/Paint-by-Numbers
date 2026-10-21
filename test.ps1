param([switch]$CoreOnly)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    if (-not $env:UV_PROJECT_ENVIRONMENT) {
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'PaintByNumbers\venv'
    }
    $testArguments = @('-m', 'pytest', '-q')
    if ($CoreOnly) { $testArguments += 'tests/test_core.py' }
    uv run --locked python @testArguments
    if ($LASTEXITCODE -ne 0) { throw "Tests failed (exit code $LASTEXITCODE)." }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    Pop-Location
}
