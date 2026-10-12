$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}
$env:PYTHONPATH = Join-Path $PSScriptRoot "app/src"
& uv run --locked --python 3.12 python -m comic_maker @args
exit $LASTEXITCODE
