[CmdletBinding()]
param(
    [string[]]$Suite = @(),
    [switch]$ListOnly,
    [switch]$StopOnFailure
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$matrixPath = Join-Path $repoRoot "tests\phase8\regression-matrix.json"
$matrix = Get-Content -LiteralPath $matrixPath -Raw | ConvertFrom-Json
$selected = @($matrix.suites)

if ($Suite.Count -gt 0) {
    $known = @($matrix.suites | ForEach-Object { $_.id })
    $unknown = @($Suite | Where-Object { $_ -notin $known })
    if ($unknown.Count -gt 0) {
        throw "Unknown Phase 8 regression suite: $($unknown -join ', ')"
    }
    $selected = @($matrix.suites | Where-Object { $_.id -in $Suite })
}

if ($ListOnly) {
    $selected | ForEach-Object {
        Write-Output "$($_.id) [$($_.phase_scope)] $($_.executable) $($_.arguments -join ' ')"
    }
    exit 0
}

$failures = @()
foreach ($entry in $selected) {
    $workingDirectory = Join-Path $repoRoot $entry.workdir
    $candidate = Join-Path $workingDirectory $entry.executable
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $executable = $candidate
    }
    else {
        $command = Get-Command $entry.executable -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            $failures += "$($entry.id): executable not found ($($entry.executable))"
            if ($StopOnFailure) { break }
            continue
        }
        $executable = $command.Source
    }

    Write-Output "RUN $($entry.id) [$($entry.phase_scope)]"
    $environmentBackup = @{}
    if ($null -ne $entry.environment) {
        foreach ($property in $entry.environment.PSObject.Properties) {
            $environmentBackup[$property.Name] = [Environment]::GetEnvironmentVariable(
                $property.Name,
                "Process"
            )
            [Environment]::SetEnvironmentVariable(
                $property.Name,
                [string]$property.Value,
                "Process"
            )
        }
    }
    Push-Location $workingDirectory
    try {
        & $executable @($entry.arguments)
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
        foreach ($name in $environmentBackup.Keys) {
            [Environment]::SetEnvironmentVariable(
                $name,
                $environmentBackup[$name],
                "Process"
            )
        }
    }
    if ($exitCode -ne 0) {
        $failures += "$($entry.id): exit $exitCode"
        if ($StopOnFailure) { break }
    }
}

if ($failures.Count -gt 0) {
    Write-Error ("Phase 8 regression failures: " + ($failures -join "; "))
    exit 1
}

Write-Output "Phase 8 selected regression suites passed."
