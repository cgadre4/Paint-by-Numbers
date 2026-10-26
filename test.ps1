param([switch]$CoreOnly)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
$previousNode = $env:PLAYWRIGHT_NODEJS_PATH
try {
    if (-not $env:UV_PROJECT_ENVIRONMENT) {
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'PaintByNumbers\venv-1.3'
    }
    if (-not $env:PLAYWRIGHT_NODEJS_PATH) {
        $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
        if ($nodeCommand) { $env:PLAYWRIGHT_NODEJS_PATH = $nodeCommand.Source }
    }
    $testArguments = @('-m', 'pytest', '-q')
    if ($CoreOnly) { $testArguments += 'tests/test_core.py' }
    uv run --locked python @testArguments
    if ($LASTEXITCODE -ne 0) { throw "Tests failed (exit code $LASTEXITCODE)." }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    $env:PLAYWRIGHT_NODEJS_PATH = $previousNode
    Pop-Location
}
