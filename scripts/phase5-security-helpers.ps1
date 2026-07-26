function New-Phase5FileSecurity {
    $security = [Security.AccessControl.FileSecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
    foreach ($sid in @(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        [Security.Principal.SecurityIdentifier]::new("S-1-5-18"),
        [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    )) {
        $security.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                $sid,
                [Security.AccessControl.FileSystemRights]::FullControl,
                [Security.AccessControl.AccessControlType]::Allow
            )
        ) | Out-Null
    }
    return $security
}

function New-Phase5DirectorySecurity {
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner([Security.Principal.WindowsIdentity]::GetCurrent().User)
    foreach ($sid in @(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        [Security.Principal.SecurityIdentifier]::new("S-1-5-18"),
        [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    )) {
        $security.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                $sid,
                [Security.AccessControl.FileSystemRights]::FullControl,
                [Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit",
                [Security.AccessControl.PropagationFlags]::None,
                [Security.AccessControl.AccessControlType]::Allow
            )
        ) | Out-Null
    }
    return $security
}

function Assert-Phase5PathNotReparse {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    if (Test-Path -LiteralPath $LiteralPath) {
        $item = Get-Item -LiteralPath $LiteralPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to use reparse path: $LiteralPath"
        }
    }
}

function Protect-Phase5StateDirectory {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    Assert-Phase5PathNotReparse -LiteralPath $LiteralPath
    $security = New-Phase5DirectorySecurity
    if (Test-Path -LiteralPath $LiteralPath) {
        $item = Get-Item -LiteralPath $LiteralPath -Force
        if (-not $item.PSIsContainer) {
            throw "Expected a directory: $LiteralPath"
        }
        [IO.FileSystemAclExtensions]::SetAccessControl(
            [IO.DirectoryInfo]$item,
            $security
        )
    }
    else {
        [IO.FileSystemAclExtensions]::CreateDirectory($security, $LiteralPath) | Out-Null
    }

    $expectedOwner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $expectedSids = @(
        $expectedOwner.Value,
        "S-1-5-18",
        "S-1-5-32-544"
    )
    $applied = [IO.FileSystemAclExtensions]::GetAccessControl(
        [IO.DirectoryInfo]::new($LiteralPath)
    )
    $actualOwner = $applied.GetOwner(
        [Security.Principal.SecurityIdentifier]
    )
    $rules = @($applied.GetAccessRules(
        $true,
        $false,
        [Security.Principal.SecurityIdentifier]
    ))
    $invalidRule = $rules | Where-Object {
        $_.IsInherited -or
        $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $_.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $_.IdentityReference.Value -notin $expectedSids
    }
    if (
        $actualOwner.Value -ne $expectedOwner.Value -or
        $rules.Count -ne $expectedSids.Count -or
        $invalidRule
    ) {
        throw "StateRoot ownership or ACL verification failed: $LiteralPath"
    }
}

function Write-Phase5RestrictedBytes {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )

    Assert-Phase5PathNotReparse -LiteralPath $LiteralPath
    if (Test-Path -LiteralPath $LiteralPath) {
        $item = Get-Item -LiteralPath $LiteralPath -Force
        if ($item.PSIsContainer) {
            throw "Expected a file: $LiteralPath"
        }
        Remove-Item -LiteralPath $LiteralPath -Force
    }
    $stream = [IO.FileSystemAclExtensions]::Create(
        [IO.FileInfo]::new($LiteralPath),
        [IO.FileMode]::CreateNew,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [IO.FileShare]::None,
        4096,
        [IO.FileOptions]::None,
        (New-Phase5FileSecurity)
    )
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
    }
    finally {
        $stream.Dispose()
    }
}

function Write-Phase5RestrictedText {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$Text
    )

    Write-Phase5RestrictedBytes `
        -LiteralPath $LiteralPath `
        -Bytes ([Text.UTF8Encoding]::new($false).GetBytes($Text))
}
