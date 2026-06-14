$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path (Split-Path -Parent $PSScriptRoot) ".uv-cache"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
        [string[]] $Command
    )

    $Executable = $Command[0]
    $Arguments = if ($Command.Length -gt 1) { $Command[1..($Command.Length - 1)] } else { @() }
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Invoke-CheckedCommand uv run ruff format --check .
Invoke-CheckedCommand uv run ruff check .
Invoke-CheckedCommand uv run ty check
Invoke-CheckedCommand uv run pytest
