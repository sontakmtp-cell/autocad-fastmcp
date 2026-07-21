[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$interface = [Environment]::GetEnvironmentVariable("AUTOCAD_MCP_INTERFACE", "Process")
if ([string]::IsNullOrWhiteSpace($interface)) {
    $interface = "legacy"
}
$interface = $interface.Trim().ToLowerInvariant()

switch ($interface) {
    "legacy" {
        $pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $pythonPath) {
            & $pythonPath -m autocad_mcp
        }
        else {
            & uv run --project $repoRoot python -m autocad_mcp
        }
    }
    "public_v1" {
        & uv run --project (Join-Path $repoRoot "services\gateway") --locked python -m autocad_gateway
    }
    "dual" {
        throw "AUTOCAD_MCP_INTERFACE=dual is not supported by the local launcher. Choose legacy or public_v1."
    }
    default {
        throw "Unsupported AUTOCAD_MCP_INTERFACE='$interface'. Choose legacy or public_v1."
    }
}
