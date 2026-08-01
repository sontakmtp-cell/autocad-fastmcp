[CmdletBinding()]
param(
    [string]$Configuration = "Release",
    [string]$DotNetPath = "dotnet",
    [string]$AutoCADReferencePath = "C:\Program Files\Autodesk\AutoCAD 2025"
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sourceRoot = Join-Path $repoRoot "native\autocad_managed_host"
$solution = Join-Path $sourceRoot "AutocadMcp.ManagedHost.sln"
$project = Join-Path $sourceRoot "src\AutocadMcp.Host.R25\AutocadMcp.Host.R25.csproj"
$outputRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $repoRoot "dist\phase8-managed-host")
)
$bundleRoot = Join-Path $outputRoot "AutocadMcp.ManagedHost.R25.bundle"
$r25Root = Join-Path $bundleRoot "Contents\R25"
$sharedRoot = Join-Path $bundleRoot "Contents\Shared"

if (-not (Get-Command $DotNetPath -ErrorAction SilentlyContinue)) {
    throw "The required .NET SDK was not found: $DotNetPath"
}
foreach ($reference in @("acmgd.dll", "acdbmgd.dll", "accoremgd.dll")) {
    $referencePath = Join-Path $AutoCADReferencePath $reference
    if (-not (Test-Path -LiteralPath $referencePath -PathType Leaf)) {
        throw "The AutoCAD 2025 reference is missing: $referencePath"
    }
}

$phase8Pack = Join-Path $sourceRoot `
    "src\AutocadMcp.Host.R25\Phase8ManagedOperationPack.cs"
$dispatcherCallers = Get-ChildItem -LiteralPath (
    Join-Path $sourceRoot "src\AutocadMcp.Host.R25"
) -Filter "*.cs" -File |
    Where-Object { $_.FullName -ne $phase8Pack } |
    Select-String -Pattern "Phase8ManagedOperationPack\."
if (-not $dispatcherCallers) {
    throw (
        "Phase 8 R25 build is blocked: the actual Host dispatcher does not " +
        "invoke Phase8ManagedOperationPack."
    )
}

$repoPrefix = $repoRoot + [System.IO.Path]::DirectorySeparatorChar
if (-not $outputRoot.StartsWith(
    $repoPrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
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

& $DotNetPath publish $project `
    --configuration $Configuration `
    --no-restore `
    --output $r25Root `
    -p:AutoCADReferencePath="$AutoCADReferencePath"
if ($LASTEXITCODE -ne 0) {
    throw "Managed Host R25 publish failed."
}

New-Item -ItemType Directory -Path $sharedRoot -Force | Out-Null
$packageContents = @'
<?xml version="1.0" encoding="utf-8"?>
<ApplicationPackage
  SchemaVersion="1.0"
  AppVersion="0.8.0"
  ProductCode="{C55DC577-B777-4C96-8A9B-1C958EADAE0C}"
  UpgradeCode="{1B451DAC-0E9D-4092-8D55-6D29593FBD71}"
  Name="AutoCAD MCP Managed Host R25 Phase 8 Lab"
  Description="Allowlisted Phase 8 Managed .NET operation pack for local R25 evidence."
  Author="Ky Thuat Vang">
  <CompanyDetails Name="Ky Thuat Vang" />
  <RuntimeRequirements OS="Win64" Platform="AutoCAD|ACADM" SeriesMin="R25.0" SeriesMax="R25.0" />
  <Components>
    <ComponentEntry
      AppName="AutoCAD MCP Managed Host R25"
      AppDescription="Allowlisted cad.host/1 and sealed Phase 8 operation pack."
      AppType=".Net"
      ModuleName="./Contents/R25/AutocadMcp.Host.R25.dll"
      PerDocument="False">
      <LoadReasons LoadOnAutoCADStartup="True" />
      <Commands GroupName="AUTOCADMCP">
        <Command Global="AUTOCADMCPSTATUS" Local="AUTOCADMCPSTATUS" />
      </Commands>
    </ComponentEntry>
  </Components>
</ApplicationPackage>
'@
[System.IO.File]::WriteAllText(
    (Join-Path $bundleRoot "PackageContents.xml"),
    $packageContents,
    (New-Object System.Text.UTF8Encoding $false)
)

$artifactFiles = Get-ChildItem -LiteralPath $r25Root -File |
    Where-Object {
        $_.Extension -in @(".dll", ".pdb") -or
        $_.Name.EndsWith(".deps.json", [System.StringComparison]::OrdinalIgnoreCase)
    } |
    Sort-Object Name
$artifactHashes = [ordered]@{}
foreach ($artifact in $artifactFiles) {
    $artifactHashes[$artifact.Name] = (
        Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256
    ).Hash.ToLowerInvariant()
}
$aggregateText = (
    $artifactHashes.GetEnumerator() |
    ForEach-Object { "$($_.Key):$($_.Value)" }
) -join "`n"
$aggregateBytes = [System.Text.Encoding]::UTF8.GetBytes($aggregateText)
$aggregateHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $aggregateHash = (
        [System.BitConverter]::ToString(
            $aggregateHasher.ComputeHash($aggregateBytes)
        )
    ).Replace("-", "").ToLowerInvariant()
}
finally {
    $aggregateHasher.Dispose()
}
$manifest = [ordered]@{
    schema_version = "cad.package-manifest/1"
    package_id = "autocad.managed_host.r25"
    package_version = "0.8.0"
    host_family = "R25"
    target_framework = "net8.0-windows"
    supported_products = @("AutoCAD 2025", "AutoCAD Mechanical 2025")
    supported_series = @("R25.0")
    supported_os = @("Windows x64")
    operation_registry = "cad.operation-registry/1"
    phase8_scope = @("create-equivalent", "exact-transform", "checkpoint-v2")
    extension_packs_enabled = @()
    signed = $false
    package_hash = "sha256:$aggregateHash"
    artifacts = $artifactHashes
}
$manifest | ConvertTo-Json -Depth 6 |
    ForEach-Object {
        [System.IO.File]::WriteAllText(
            (Join-Path $sharedRoot "package-manifest.json"),
            $_,
            (New-Object System.Text.UTF8Encoding $false)
        )
    }

Write-Host "Tests passed and unsigned Phase 8 local R25 lab bundle built:"
Write-Host $bundleRoot
