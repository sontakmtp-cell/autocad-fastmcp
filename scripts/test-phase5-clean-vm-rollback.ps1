[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$VMName,
    [Parameter(Mandatory)]
    [pscredential]$Credential,
    [Parameter(Mandatory)]
    [string]$ReleaseV1Root,
    [Parameter(Mandatory)]
    [string]$ReleaseV2Root,
    [string]$EvidencePath
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($EvidencePath)) {
    $EvidencePath = Join-Path $PSScriptRoot "..\dist\phase5-clean-vm-evidence.json"
}
$evidenceFile = [System.IO.Path]::GetFullPath($EvidencePath)
$vm = Get-VM -Name $VMName -ErrorAction Stop
if ($vm.State -ne "Running") {
    throw "Clean VM must already be running; this harness does not change VM power state."
}

$session = New-PSSession -VMName $VMName -Credential $Credential
$guestRoot = "C:\Phase5CleanVm\$([Guid]::NewGuid().ToString('N'))"
try {
    Invoke-Command -Session $session -ScriptBlock {
        param($Root)
        if (Test-Path -LiteralPath $Root) {
            throw "Guest clean root already exists."
        }
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
    } -ArgumentList $guestRoot
    Copy-Item -LiteralPath $ReleaseV1Root -Destination "$guestRoot\release-v1" `
        -ToSession $session -Recurse
    Copy-Item -LiteralPath $ReleaseV2Root -Destination "$guestRoot\release-v2" `
        -ToSession $session -Recurse
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "test-phase5-install-rollback.ps1") `
        -Destination "$guestRoot\test-phase5-install-rollback.ps1" -ToSession $session

    $guestEvidence = "$guestRoot\evidence.json"
    $result = Invoke-Command -Session $session -ScriptBlock {
        param($Root, $Evidence)
        & "$Root\test-phase5-install-rollback.ps1" `
            -ReleaseV1Root "$Root\release-v1" `
            -ReleaseV2Root "$Root\release-v2" `
            -WorkRoot "$Root\work" `
            -EvidencePath $Evidence
    } -ArgumentList $guestRoot, $guestEvidence
    Copy-Item -FromSession $session -LiteralPath $guestEvidence -Destination $evidenceFile
    $result
}
finally {
    if ($session) {
        Remove-PSSession $session
    }
}
