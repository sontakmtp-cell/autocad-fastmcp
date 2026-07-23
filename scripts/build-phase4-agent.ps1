[CmdletBinding()]
param(
    [string]$OutputRoot = (Join-Path $PSScriptRoot '..\dist\phase4-agent'),
    [string]$PythonVersion = '3.12',
    [switch]$SkipSync
)

$ErrorActionPreference = 'Stop'
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$agentRoot = Join-Path $repoRoot 'apps\desktop_agent'
$launcher = Join-Path $agentRoot 'launcher.py'
$buildRoot = Join-Path $agentRoot 'build\phase4-agent'
$package = Join-Path $repoRoot 'lisp-code\mcp_dispatch.lsp'
$output = [IO.Path]::GetFullPath($OutputRoot)
$volumeRoot = [IO.Path]::GetPathRoot($output)
if ($output.TrimEnd('\') -eq $volumeRoot.TrimEnd('\')) {
    throw 'OutputRoot khong duoc la thu muc goc cua o dia.'
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
if (Test-Path -LiteralPath $buildRoot) {
    $resolvedBuildRoot = (Resolve-Path -LiteralPath $buildRoot).Path
    if (-not $resolvedBuildRoot.StartsWith($agentRoot.TrimEnd('\') + '\')) {
        throw "Tu choi don build folder ngoai AgentRoot: $resolvedBuildRoot"
    }
    Remove-Item -LiteralPath $resolvedBuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

Push-Location $agentRoot
try {
    if (-not $SkipSync) {
        Write-Host "[$(Get-Date -Format o)] Syncing standalone build dependencies"
        uv sync --locked --python $PythonVersion --group build --group test --group ui-test
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }
    }

    Write-Host "[$(Get-Date -Format o)] Nuitka environment"
    uv run --no-sync python -m nuitka --version
    if ($LASTEXITCODE -ne 0) { throw "Nuitka version check failed with exit code $LASTEXITCODE" }

    $stdoutPath = Join-Path $buildRoot 'nuitka.stdout.log'
    $stderrPath = Join-Path $buildRoot 'nuitka.stderr.log'
    $uvCommand = (Get-Command uv -ErrorAction Stop).Source
    $nuitkaArgs = @(
        'run',
        '--no-sync',
        'python',
        '-m',
        'nuitka',
        '--mode=standalone',
        '--enable-plugin=pyside6',
        '--msvc=latest',
        '--assume-yes-for-downloads',
        '--windows-console-mode=disable',
        '--show-progress',
        "--output-dir=$buildRoot",
        '--output-filename=KythuatvangAutoCADAgent.exe',
        $launcher
    )

    Write-Host "[$(Get-Date -Format o)] Starting standalone compilation"
    $process = Start-Process `
        -FilePath $uvCommand `
        -ArgumentList $nuitkaArgs `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    if (Test-Path -LiteralPath $stdoutPath) {
        Get-Content -LiteralPath $stdoutPath | ForEach-Object { Write-Host $_ }
    }
    if (Test-Path -LiteralPath $stderrPath) {
        Get-Content -LiteralPath $stderrPath | ForEach-Object { Write-Host $_ }
    }
    if ($process.ExitCode -ne 0) {
        throw "Nuitka standalone build failed with exit code $($process.ExitCode)"
    }
    Write-Host "[$(Get-Date -Format o)] Standalone compilation completed"
}
finally {
    Pop-Location
}

$packageDir = Join-Path $output 'packages\autocad.lisp.drawing_info\3.3-c1'
New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
Copy-Item -LiteralPath $package -Destination (Join-Path $packageDir 'mcp_dispatch.lsp') -Force
$packageHash = (Get-FileHash -LiteralPath (Join-Path $packageDir 'mcp_dispatch.lsp') -Algorithm SHA256).Hash.ToLowerInvariant()

$built = Get-ChildItem -LiteralPath $buildRoot -Recurse -File |
    Where-Object { $_.Name -in @('KythuatvangAutoCADAgent.exe', 'launcher.exe') } |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
if (-not $built) { throw 'Nuitka khong tao duoc KythuatvangAutoCADAgent.exe' }
$standaloneDir = $built.Directory.FullName
$appOutput = Join-Path $output 'app'
if (Test-Path -LiteralPath $appOutput) {
    $resolvedAppOutput = (Resolve-Path -LiteralPath $appOutput).Path
    if (-not $resolvedAppOutput.StartsWith($output.TrimEnd('\') + '\')) {
        throw "Tu choi don app folder ngoai OutputRoot: $resolvedAppOutput"
    }
    Remove-Item -LiteralPath $resolvedAppOutput -Recurse -Force
}
Copy-Item -LiteralPath $standaloneDir -Destination $appOutput -Recurse -Force
$artifactTarget = Join-Path $appOutput 'KythuatvangAutoCADAgent.exe'
if ($built.Name -ne 'KythuatvangAutoCADAgent.exe') {
    Move-Item -LiteralPath (Join-Path $appOutput $built.Name) -Destination $artifactTarget -Force
}
$artifactHash = (Get-FileHash -LiteralPath $artifactTarget -Algorithm SHA256).Hash.ToLowerInvariant()
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'provision-phase4-agent.ps1') -Destination $output -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-phase4-agent.ps1') -Destination $output -Force

$manifest = [ordered]@{
    schema = 'cad.agent.release/1'
    agent_version = '0.1.0'
    mode = 'standalone'
    artifact = 'app/KythuatvangAutoCADAgent.exe'
    artifact_sha256 = $artifactHash
    launch_script = 'run-phase4-agent.ps1'
    provision_script = 'provision-phase4-agent.ps1'
    package = [ordered]@{
        package_id = 'autocad.lisp.drawing_info'
        version = '3.3-c1'
        path = 'packages/autocad.lisp.drawing_info/3.3-c1/mcp_dispatch.lsp'
        sha256 = $packageHash
    }
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $output 'manifest.json') -Encoding UTF8
Write-Host "Standalone folder: $output"
Write-Host "Agent SHA-256: $artifactHash"
Write-Host "Package SHA-256: $packageHash"
