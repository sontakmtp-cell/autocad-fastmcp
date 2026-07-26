[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$DotNetPath = "C:\Users\haing\AppData\Local\Codex\autocad-phase5-dotnet-sdk\dotnet.exe",
    [string]$AutoCADReferencePath = "C:\Program Files\Autodesk\AutoCAD 2025"
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sourceRoot = Join-Path $repoRoot "native\autocad_managed_host"
$solution = Join-Path $sourceRoot "AutocadMcp.ManagedHost.sln"
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist\phase5-managed-host"))
$bundleRoot = Join-Path $outputRoot "AutocadMcp.ManagedHost.R25.bundle"
$r25Root = Join-Path $bundleRoot "Contents\R25"
$sharedRoot = Join-Path $bundleRoot "Contents\Shared"

if (-not (Test-Path -LiteralPath $DotNetPath -PathType Leaf)) {
    throw "The required .NET SDK was not found: $DotNetPath"
}
foreach ($reference in @("acmgd.dll", "acdbmgd.dll", "accoremgd.dll")) {
    $referencePath = Join-Path $AutoCADReferencePath $reference
    if (-not (Test-Path -LiteralPath $referencePath -PathType Leaf)) {
        throw "The AutoCAD 2025 reference is missing: $referencePath"
    }
}

if (-not $outputRoot.StartsWith($repoRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean an output path outside the repository."
}
if (Test-Path -LiteralPath $outputRoot) {
    Remove-Item -LiteralPath $outputRoot -Recurse -Force
}

& $DotNetPath test $solution `
    --configuration $Configuration `
    -p:AutoCADReferencePath="$AutoCADReferencePath"
if ($LASTEXITCODE -ne 0) {
    throw "Managed Host tests failed."
}

& $DotNetPath publish (Join-Path $sourceRoot "src\AutocadMcp.Host.R25\AutocadMcp.Host.R25.csproj") `
    --configuration $Configuration `
    --no-restore `
    --output $r25Root `
    -p:AutoCADReferencePath="$AutoCADReferencePath"
if ($LASTEXITCODE -ne 0) {
    throw "Managed Host publish failed."
}

New-Item -ItemType Directory -Path $sharedRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot "bundle\PackageContents.xml") `
    -Destination (Join-Path $bundleRoot "PackageContents.xml")

$artifactFiles = Get-ChildItem -LiteralPath $r25Root -File |
    Where-Object { $_.Extension -in @(".dll", ".pdb") -or $_.Name.EndsWith(".deps.json", [System.StringComparison]::OrdinalIgnoreCase) } |
    Sort-Object Name
$artifactHashes = [ordered]@{}
foreach ($artifact in $artifactFiles) {
    $artifactHashes[$artifact.Name] = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$aggregateText = ($artifactHashes.GetEnumerator() | ForEach-Object { "$($_.Key):$($_.Value)" }) -join "`n"
$aggregateBytes = [System.Text.Encoding]::UTF8.GetBytes($aggregateText)
$aggregateHashBytes = [System.Security.Cryptography.SHA256]::HashData($aggregateBytes)
$aggregateHash = [System.Convert]::ToHexString($aggregateHashBytes).ToLowerInvariant()
$manifest = [ordered]@{
    package_id = "autocad.managed_host.r25"
    package_version = "0.1.0"
    host_family = "R25"
    target_framework = "net8.0-windows"
    supported_products = @("AutoCAD 2025", "AutoCAD Mechanical 2025")
    supported_series = @("R25.0")
    supported_os = @("Windows x64")
    signed = $false
    package_hash = "sha256:$aggregateHash"
    artifacts = $artifactHashes
}
$manifest | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $sharedRoot "package-manifest.json") -Encoding utf8NoBOM

Write-Host "Tests passed and unsigned lab bundle built:"
Write-Host $bundleRoot
