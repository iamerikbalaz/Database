$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'e2e-private-process.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
. (Join-Path $PSScriptRoot 'e2e-runner-operations.ps1')

function ConvertTo-E2eCanonicalPath {
    param([Parameter(Mandatory)] [string] $Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $pathRoot
    }
    return $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-E2ePathWithinRoot {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $Candidate
    )

    $rootPath = ConvertTo-E2eCanonicalPath $Root
    $candidatePath = ConvertTo-E2eCanonicalPath $Candidate
    if ($candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $rootPrefix = if ($rootPath.EndsWith([string][System.IO.Path]::DirectorySeparatorChar)) {
        $rootPath
    }
    else {
        $rootPath + [System.IO.Path]::DirectorySeparatorChar
    }
    return $candidatePath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-E2eNoReparsePath {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $Target
    )

    $rootPath = ConvertTo-E2eCanonicalPath $Root
    $targetPath = ConvertTo-E2eCanonicalPath $Target
    if (-not (Test-E2ePathWithinRoot -Root $rootPath -Candidate $targetPath)) {
        throw "Path is outside the trusted root '$rootPath': $targetPath"
    }

    $rootItem = Get-Item -LiteralPath $rootPath -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse point requires manual review; automatic E2E writes and cleanup are disabled: $rootPath"
    }

    if ($targetPath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }
    $rootPrefix = if ($rootPath.EndsWith([string][System.IO.Path]::DirectorySeparatorChar)) {
        $rootPath
    }
    else {
        $rootPath + [System.IO.Path]::DirectorySeparatorChar
    }
    $relative = $targetPath.Substring($rootPrefix.Length)
    $current = $rootPath
    $missingSeen = $false
    foreach ($component in @($relative -split '[\\/]' | Where-Object { $_ })) {
        $current = Join-Path $current $component
        $exists = Test-Path -LiteralPath $current -ErrorAction Stop
        if (-not $exists) {
            $missingSeen = $true
            continue
        }
        if ($missingSeen) {
            throw "A child exists below a missing path component; manual review is required: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse point requires manual review; automatic E2E writes and cleanup are disabled: $current"
        }
    }
}

function Assert-LocalE2eRepositoryRoot {
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Nullable[System.IO.DriveType]] $DriveTypeOverride
    )

    $root = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if ($root.StartsWith('\\', [System.StringComparison]::Ordinal)) {
        throw "E2E cannot run from a UNC repository root: $root"
    }
    if ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT) {
        $pathRoot = [System.IO.Path]::GetPathRoot($root)
        $driveType = if ($null -ne $DriveTypeOverride) {
            $DriveTypeOverride.Value
        }
        else {
            ([System.IO.DriveInfo]::new($pathRoot)).DriveType
        }
        if ($driveType -ne [System.IO.DriveType]::Fixed) {
            throw "E2E repository must be on a local Fixed drive; '$pathRoot' is '$driveType'."
        }
        Assert-E2eNoReparsePath -Root $pathRoot -Target $root
    }
    elseif ($null -ne $DriveTypeOverride -and $DriveTypeOverride.Value -ne [System.IO.DriveType]::Fixed) {
        throw "E2E repository must be on a local Fixed drive."
    }
    return $root
}

function Assert-E2eManagedRunPath {
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $ManagedRoot,
        [Parameter(Mandatory)] [guid] $RunGuid,
        [Parameter(Mandatory)] [string] $RunPath,
        [switch] $RequireMarker
    )

    $repository = [System.IO.Path]::GetFullPath($RepositoryRoot)
    $managed = [System.IO.Path]::GetFullPath($ManagedRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $target = [System.IO.Path]::GetFullPath($RunPath).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (-not (Test-E2ePathWithinRoot -Root $repository -Candidate $managed)) {
        throw "Managed E2E root is outside the repository: $managed"
    }
    $expected = [System.IO.Path]::GetFullPath((Join-Path $managed $RunGuid.ToString('D'))).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (-not $target.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "E2E run path must be the exact GUID child of '$managed': $target"
    }
    Assert-E2eNoReparsePath -Root $repository -Target $target
    if ($RequireMarker) {
        $marker = Join-Path $target '.reawote-e2e-run'
        Assert-E2eNoReparsePath -Root $repository -Target $marker
        if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
            throw "Refusing cleanup because the E2E ownership marker is missing: $marker"
        }
        $expectedMarker = "reawote-e2e-owned-v2:$($RunGuid.ToString('D'))"
        if ([System.IO.File]::ReadAllText($marker) -ne $expectedMarker) {
            throw "Refusing cleanup because the E2E ownership marker is invalid: $marker"
        }
    }
    return $target
}

function New-E2eManagedRunDirectory {
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $ManagedRoot,
        [Parameter(Mandatory)] [guid] $RunGuid
    )

    $repository = [System.IO.Path]::GetFullPath($RepositoryRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $managed = [System.IO.Path]::GetFullPath($ManagedRoot)
    $runPath = Join-Path $managed $RunGuid.ToString('D')
    [void](Assert-E2eManagedRunPath -RepositoryRoot $repository -ManagedRoot $managed -RunGuid $RunGuid -RunPath $runPath)

    $relative = $runPath.Substring(($repository + [System.IO.Path]::DirectorySeparatorChar).Length)
    $current = $repository
    foreach ($component in @($relative -split '[\\/]' | Where-Object { $_ })) {
        $current = Join-Path $current $component
        Assert-E2eNoReparsePath -Root $repository -Target $current
        if (-not (Test-Path -LiteralPath $current)) {
            [void][System.IO.Directory]::CreateDirectory($current)
        }
        Assert-E2eNoReparsePath -Root $repository -Target $current
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "Expected an E2E directory but found another item type: $current"
        }
    }
    $marker = Join-Path $runPath '.reawote-e2e-run'
    Assert-E2eNoReparsePath -Root $repository -Target $marker
    [System.IO.File]::WriteAllText(
        $marker,
        "reawote-e2e-owned-v2:$($RunGuid.ToString('D'))",
        [System.Text.UTF8Encoding]::new($false)
    )
    Assert-E2eNoReparsePath -Root $repository -Target $marker
    return $runPath
}

function New-E2eSafeDirectory {
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $RunRoot,
        [Parameter(Mandatory)] [string] $Path
    )

    if (-not (Test-E2ePathWithinRoot -Root $RunRoot -Candidate $Path)) {
        throw "E2E directory escaped its run root: $Path"
    }
    $run = ConvertTo-E2eCanonicalPath $RunRoot
    $target = ConvertTo-E2eCanonicalPath $Path
    Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $target
    $relative = $target.Substring(($run + [System.IO.Path]::DirectorySeparatorChar).Length)
    $current = $run
    foreach ($component in @($relative -split '[\\/]' | Where-Object { $_ })) {
        $current = Join-Path $current $component
        Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $current
        if (-not (Test-Path -LiteralPath $current)) {
            [void][System.IO.Directory]::CreateDirectory($current)
        }
        Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $current
        if (-not (Get-Item -LiteralPath $current -Force).PSIsContainer) {
            throw "Expected an E2E directory but found another item type: $current"
        }
    }
    return $target
}

function Write-E2eSafeTextFile {
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $RunRoot,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Content
    )

    if (-not (Test-E2ePathWithinRoot -Root $RunRoot -Candidate $Path)) {
        throw "E2E file escaped its run root: $Path"
    }
    $parent = Split-Path -Parent ([System.IO.Path]::GetFullPath($Path))
    Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $parent
    Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "E2E file parent does not exist: $parent"
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
    Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $Path
}

function Get-E2eSha256Hex {
    param([Parameter(Mandatory)] [string] $Value)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function New-E2eSyntheticPassword {
    $bytes = [byte[]]::new(32)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    # The prefix keeps the value clear of the offline common-password list. The
    # random hexadecimal suffix is shell-safe and contains no line separators.
    return "E2E!$(([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant())"
}

function Get-E2eCredentialEnvironmentNames {
    return @('E2E_AUTH_EMAIL', 'E2E_AUTH_INITIAL_PASSWORD', 'E2E_AUTH_PASSWORD', 'E2E_RUN_TOKEN')
}

function Clear-E2eCredentialEnvironment {
    foreach ($name in @(Get-E2eCredentialEnvironmentNames)) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

function Invoke-E2eWithCredentialCleanup {
    param([Parameter(Mandatory)] [scriptblock] $Action)

    try { & $Action }
    finally { Clear-E2eCredentialEnvironment }
}

function ConvertTo-E2eProcessArgument {
    param([AllowEmptyString()] [string] $Value)

    # ProcessStartInfo.ArgumentList is unavailable in Windows PowerShell 5.1.
    # Apply the Windows argv quoting rules, including trailing backslashes.
    return '"' + ([regex]::Replace($Value, '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Get-E2eSafeFailureMessage {
    param([string] $Code)

    # Do not derive messages from ErrorRecord/Exception or external output.
    switch ($Code) {
        'E2E_PREREQUISITE_FAILED' { return 'E2E_PREREQUISITE_FAILED: Required local tooling or a repository safety check failed.' }
        'E2E_DOCKER_UNAVAILABLE' { return 'E2E_DOCKER_UNAVAILABLE: Docker CLI is required; runtime tests were not run.' }
        'E2E_ISOLATION_FAILED' { return 'E2E_ISOLATION_FAILED: Isolated E2E environment validation failed.' }
        'E2E_START_FAILED' { return 'E2E_START_FAILED: The isolated E2E environment could not start.' }
        'E2E_BOOTSTRAP_FAILED' { return 'E2E_BOOTSTRAP_FAILED: Administrator provisioning could not complete.' }
        'E2E_SEED_FAILED' { return 'E2E_SEED_FAILED: Synthetic E2E data could not be prepared.' }
        'E2E_PLAYWRIGHT_FAILED' { return 'E2E_PLAYWRIGHT_FAILED: Browser tests failed; consult the sanitized scenario report.' }
        'E2E_LOG_SCAN_FAILED' { return 'E2E_LOG_SCAN_FAILED: Service logs failed the safety check or could not be read.' }
        'E2E_CLEANUP_FAILED' { return 'E2E_CLEANUP_FAILED: Isolated cleanup or protected-resource verification needs manual review.' }
        default { return 'E2E_RUN_FAILED: The isolated E2E run could not complete.' }
    }
}

function Protect-E2eDiagnosticText {
    param(
        [AllowEmptyString()] [string] $Text,
        [AllowEmptyCollection()] [string[]] $Secrets = @()
    )

    $protected = $Text
    foreach ($secret in $Secrets) {
        if (-not [string]::IsNullOrEmpty($secret)) {
            $protected = $protected.Replace($secret, '[REDACTED]')
        }
    }
    $sensitivePattern = '(?i)(csrf[_-]?token|session[_-]?token|set-cookie|authorization|__Host-reawote_session|reawote_dev_session|\$argon2(?:id)?\$)'
    return (([regex]::Split($protected, '\r?\n') | ForEach-Object {
        if ($_ -match $sensitivePattern) {
            '[redacted potentially sensitive log line]'
        }
        else {
            $_
        }
    }) -join [Environment]::NewLine)
}

function Test-E2eTextContainsSecret {
    param(
        [AllowEmptyString()] [string] $Text,
        [AllowEmptyCollection()] [string[]] $Secrets = @()
    )

    foreach ($secret in $Secrets) {
        if (-not [string]::IsNullOrEmpty($secret) -and $Text.Contains($secret)) {
            return $true
        }
    }
    return $Text -match '(?i)(csrf[_-]?token|session[_-]?token|set-cookie|authorization|__Host-reawote_session|reawote_dev_session|\$argon2(?:id)?\$)'
}

function Assert-E2eTreeNoReparse {
    param([Parameter(Mandatory)] [string] $RunRoot)

    $root = [System.IO.Path]::GetFullPath($RunRoot)
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse point requires manual review; refusing automatic cleanup of '$root': $($item.FullName)"
            }
            if ($item.PSIsContainer) {
                $pending.Push($item.FullName)
            }
        }
    }
}

function Remove-E2eManagedRunDirectory {
    param(
        [Parameter(Mandatory)] [string] $RepositoryRoot,
        [Parameter(Mandatory)] [string] $ManagedRoot,
        [Parameter(Mandatory)] [guid] $RunGuid,
        [Parameter(Mandatory)] [string] $RunPath
    )

    if (-not (Test-Path -LiteralPath $RunPath)) {
        return
    }
    $safePath = Assert-E2eManagedRunPath -RepositoryRoot $RepositoryRoot -ManagedRoot $ManagedRoot -RunGuid $RunGuid -RunPath $RunPath -RequireMarker
    Assert-E2eTreeNoReparse -RunRoot $safePath
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

function Get-E2eObjectPropertyValue {
    param(
        $Object,
        [Parameter(Mandatory)] [string] $Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Assert-E2eEngineVolumeInspection {
    param(
        [Parameter(Mandatory)] $Inspection,
        [Parameter(Mandatory)] [string] $ExpectedName,
        [Parameter(Mandatory)] [string] $ExpectedProjectName,
        [string] $ExpectedLogicalVolumeName = 'postgres_data'
    )

    $name = [string](Get-E2eObjectPropertyValue $Inspection 'Name')
    if (-not $name.Equals($ExpectedName, [System.StringComparison]::Ordinal)) {
        throw 'E2E Docker engine volume has an unexpected name.'
    }
    $driver = [string](Get-E2eObjectPropertyValue $Inspection 'Driver')
    if (-not $driver.Equals('local', [System.StringComparison]::Ordinal)) {
        throw "E2E Docker engine volume must use Driver='local'."
    }
    $scope = [string](Get-E2eObjectPropertyValue $Inspection 'Scope')
    if (-not $scope.Equals('local', [System.StringComparison]::Ordinal)) {
        throw "E2E Docker engine volume must use Scope='local'."
    }

    $optionsProperty = $Inspection.PSObject.Properties['Options']
    if ($null -eq $optionsProperty) {
        throw 'E2E Docker engine volume must expose Options as null or an empty object.'
    }
    $options = $optionsProperty.Value
    $hasOptions = if ($null -eq $options) {
        $false
    }
    elseif ($options -is [System.Collections.IDictionary]) {
        $options.Count -ne 0
    }
    elseif ($options -is [pscustomobject]) {
        @($options.PSObject.Properties).Count -ne 0
    }
    else {
        $true
    }
    if ($hasOptions) {
        throw 'E2E Docker engine volume has non-empty Options; refusing all database mutations.'
    }

    $labels = Get-E2eObjectPropertyValue $Inspection 'Labels'
    $projectLabel = [string](Get-E2eObjectPropertyValue $labels 'com.docker.compose.project')
    $volumeLabel = [string](Get-E2eObjectPropertyValue $labels 'com.docker.compose.volume')
    if (-not $projectLabel.Equals($ExpectedProjectName, [System.StringComparison]::Ordinal) -or
        -not $volumeLabel.Equals($ExpectedLogicalVolumeName, [System.StringComparison]::Ordinal)) {
        throw 'E2E Docker engine volume does not have the exact expected Compose ownership labels.'
    }
    return $ExpectedName
}

function Invoke-E2eGuardedAction {
    param(
        [Parameter(Mandatory)] [scriptblock] $Validation,
        [Parameter(Mandatory)] [scriptblock] $Action
    )

    & $Validation
    & $Action
}

function ConvertTo-E2eSafeMountField {
    param($Value)

    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return '<missing>'
    }
    $safe = ([string]$Value) -replace '[\x00-\x1f\x7f]', '?'
    if ($safe.Length -gt 160) {
        $safe = $safe.Substring(0, 157) + '...'
    }
    return $safe.Replace("'", "''")
}

function Format-E2eComposeMounts {
    param([object[]] $Mounts)

    if ($null -eq $Mounts -or $Mounts.Count -eq 0) {
        return '<none>'
    }
    return (@($Mounts | ForEach-Object {
        $typeValue = Get-E2eObjectPropertyValue $_ 'type'
        $sourceValue = if ([string]$typeValue -eq 'volume') {
            Get-E2eObjectPropertyValue $_ 'source'
        }
        else {
            '<redacted-non-volume-source>'
        }
        $type = ConvertTo-E2eSafeMountField $typeValue
        $source = ConvertTo-E2eSafeMountField $sourceValue
        $target = ConvertTo-E2eSafeMountField (Get-E2eObjectPropertyValue $_ 'target')
        "type='$type', source='$source', target='$target'"
    }) -join '; ')
}

function Format-E2eRuntimeMounts {
    param([object[]] $Mounts)

    if ($null -eq $Mounts -or $Mounts.Count -eq 0) {
        return '<none>'
    }
    return (@($Mounts | ForEach-Object {
        $typeValue = Get-E2eObjectPropertyValue $_ 'Type'
        $sourceValue = if ([string]$typeValue -eq 'volume') {
            Get-E2eObjectPropertyValue $_ 'Name'
        }
        else {
            '<redacted-non-volume-source>'
        }
        $type = ConvertTo-E2eSafeMountField $typeValue
        $source = ConvertTo-E2eSafeMountField $sourceValue
        $target = ConvertTo-E2eSafeMountField (Get-E2eObjectPropertyValue $_ 'Destination')
        "type='$type', source='$source', target='$target'"
    }) -join '; ')
}

function Assert-E2eComposeDatabaseVolume {
    param(
        [Parameter(Mandatory)] $Configuration,
        [Parameter(Mandatory)] [string] $ExpectedEngineName,
        [string] $ExpectedTarget = '/var/lib/postgresql'
    )

    $servicesProperty = $Configuration.PSObject.Properties['services']
    if ($null -eq $servicesProperty) {
        throw "Rendered Compose configuration has no services object."
    }
    $database = $servicesProperty.Value.PSObject.Properties['database']
    if ($null -eq $database) {
        throw "Rendered Compose configuration has no database service."
    }
    $mountsProperty = $database.Value.PSObject.Properties['volumes']
    $mounts = @()
    if ($null -ne $mountsProperty) {
        $mounts = @($mountsProperty.Value)
    }
    $mountSummary = Format-E2eComposeMounts $mounts
    if ($mounts.Count -ne 1) {
        throw "Database must define exactly one mount at '$ExpectedTarget'; found: $mountSummary."
    }
    $mount = $mounts[0]
    $mountTarget = [string](Get-E2eObjectPropertyValue $mount 'target')
    if ($mountTarget -ne $ExpectedTarget) {
        throw "Database mount must target '$ExpectedTarget'; found: $mountSummary."
    }
    $mountType = [string](Get-E2eObjectPropertyValue $mount 'type')
    if ($mountType -ne 'volume') {
        throw "Database data mount must have type='volume'; found: $mountSummary."
    }
    $logicalSource = [string](Get-E2eObjectPropertyValue $mount 'source')
    if ([string]::IsNullOrWhiteSpace($logicalSource)) {
        throw "Database data mount must use a named logical Compose volume source; found: $mountSummary."
    }
    $volumesProperty = $Configuration.PSObject.Properties['volumes']
    if ($null -eq $volumesProperty) {
        throw "Rendered Compose configuration has no top-level volumes object; database mounts: $mountSummary."
    }
    $volumeProperty = $volumesProperty.Value.PSObject.Properties[$logicalSource]
    if ($null -eq $volumeProperty) {
        $safeLogicalSource = ConvertTo-E2eSafeMountField $logicalSource
        throw "Logical database volume '$safeLogicalSource' is missing from top-level volumes; database mounts: $mountSummary."
    }
    $nameProperty = $volumeProperty.Value.PSObject.Properties['name']
    if ($null -eq $nameProperty -or $nameProperty.Value -ne $ExpectedEngineName) {
        $safeLogicalSource = ConvertTo-E2eSafeMountField $logicalSource
        $actualEngineName = if ($null -eq $nameProperty) { '<missing>' } else { ConvertTo-E2eSafeMountField $nameProperty.Value }
        throw "Logical database volume '$safeLogicalSource' resolves to engine volume '$actualEngineName', expected '$ExpectedEngineName'; database mounts: $mountSummary."
    }
    $externalProperty = $volumeProperty.Value.PSObject.Properties['external']
    if ($null -ne $externalProperty -and $externalProperty.Value -ne $false) {
        throw "E2E database volume '$logicalSource' must not be external; database mounts: $mountSummary."
    }
    $driverProperty = $volumeProperty.Value.PSObject.Properties['driver']
    if ($null -ne $driverProperty -and -not [string]::IsNullOrWhiteSpace([string]$driverProperty.Value) -and
        $driverProperty.Value -ne 'local') {
        throw "E2E database volume '$logicalSource' must use the local Docker volume driver; database mounts: $mountSummary."
    }
    $driverOptionsProperty = $volumeProperty.Value.PSObject.Properties['driver_opts']
    if ($null -ne $driverOptionsProperty -and $null -ne $driverOptionsProperty.Value -and
        @($driverOptionsProperty.Value.PSObject.Properties).Count -gt 0) {
        throw "E2E database volume '$logicalSource' must not define driver options; database mounts: $mountSummary."
    }
    return $logicalSource
}

function Assert-E2eRuntimeDatabaseVolume {
    param(
        [Parameter(Mandatory)] [object[]] $Mounts,
        [Parameter(Mandatory)] [string] $ExpectedEngineName,
        [string] $ExpectedTarget = '/var/lib/postgresql'
    )

    $runtimeMounts = @($Mounts)
    $mountSummary = Format-E2eRuntimeMounts $runtimeMounts
    if ($runtimeMounts.Count -ne 1) {
        throw "Runtime database container must have exactly one mount at '$ExpectedTarget'; found: $mountSummary."
    }
    $mount = $runtimeMounts[0]
    $mountType = [string](Get-E2eObjectPropertyValue $mount 'Type')
    $mountName = [string](Get-E2eObjectPropertyValue $mount 'Name')
    $mountTarget = [string](Get-E2eObjectPropertyValue $mount 'Destination')
    if ($mountType -ne 'volume' -or $mountName -ne $ExpectedEngineName -or $mountTarget -ne $ExpectedTarget) {
        throw "Runtime database mount must be named volume '$ExpectedEngineName' at '$ExpectedTarget'; found: $mountSummary."
    }
    return $mountName
}

function Test-E2eLocalDockerEndpoint {
    param(
        [Parameter(Mandatory)] [string] $Endpoint,
        [Parameter(Mandatory)] [bool] $WindowsHost
    )

    if ($WindowsHost) {
        return $Endpoint -cmatch '\Anpipe:////\./pipe/[A-Za-z0-9._-]+\z'
    }
    return $Endpoint -cmatch '\Aunix:///[^\r\n\x00]+\z'
}

function Invoke-E2eDockerContextRead {
    param(
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $OperationId,
        [Parameter(Mandatory)] [string] $ResolvedExecutable
    )
    # Standalone smoke and runner use exactly the same native process path.
    Invoke-E2eReadCommand -Executable 'docker' -Arguments $Arguments `
        -OperationId $OperationId -ResolvedExecutable $ResolvedExecutable
}

function Stop-E2eDockerContextValidation {
    param([Parameter(Mandatory)] [string] $Category, $ProcessStarted = $null, $ExitCode = $null)
    Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory $Category `
        -ProcessStarted $ProcessStarted -ExitCode $ExitCode
    throw 'E2E_SAFE_OPERATION_FAILED'
}

function Assert-LocalDockerContext {
    # Also support read-only standalone callers, without changing the current
    # runner phase when this guard is repeated before a later mutation.
    if (-not (Get-Variable E2eDiagnostics -Scope Script -ErrorAction SilentlyContinue) -or
        $null -eq $script:E2eDiagnostics) {
        $script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
    }
    if ($null -eq $script:E2eDiagnostics.current_phase) {
        Start-E2eRunnerPhase -Phase 'docker_context_validation' -OperationId 'docker_executable_resolution'
    }
    $isWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    $dockerHost = [System.Environment]::GetEnvironmentVariable('DOCKER_HOST', 'Process')
    Set-E2eRunnerOperation -OperationId 'docker_context_policy_validation'
    if (-not [string]::IsNullOrWhiteSpace($dockerHost) -and
        -not (Test-E2eLocalDockerEndpoint -Endpoint $dockerHost -WindowsHost $isWindows)) {
        Stop-E2eDockerContextValidation -Category 'policy_rejected' -ProcessStarted $false
    }

    $application = Resolve-E2eReadExecutable -Executable 'docker' -OperationId 'docker_executable_resolution'
    $nameLines = @(Invoke-E2eDockerContextRead -Arguments @('context', 'show') `
        -OperationId 'docker_context_show' -ResolvedExecutable $application)
    if ($nameLines.Count -eq 0 -or ($nameLines.Count -eq 1 -and
        $nameLines[0] -is [string] -and [string]::IsNullOrWhiteSpace($nameLines[0]))) {
        Stop-E2eDockerContextValidation -Category 'empty_output' -ProcessStarted $true -ExitCode 0
    }
    # Do not concatenate lines into a different context, or allow option-like
    # names to change the meaning of the subsequent inspect invocation.
    if ($nameLines.Count -ne 1 -or $nameLines[0] -isnot [string] -or
        $nameLines[0] -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\z') {
        Stop-E2eDockerContextValidation -Category 'invalid_output' -ProcessStarted $true -ExitCode 0
    }
    $contextName = $nameLines[0]
    $jsonLines = @(Invoke-E2eDockerContextRead -Arguments @('context', 'inspect', $contextName) `
        -OperationId 'docker_context_inspect' -ResolvedExecutable $application)
    if (@($jsonLines | Where-Object { $_ -isnot [string] }).Count -ne 0) {
        Stop-E2eDockerContextValidation -Category 'invalid_output' -ProcessStarted $true -ExitCode 0
    }
    $contextJson = $jsonLines -join [System.Environment]::NewLine
    if ([string]::IsNullOrWhiteSpace($contextJson)) {
        Stop-E2eDockerContextValidation -Category 'empty_output' -ProcessStarted $true -ExitCode 0
    }

    Set-E2eRunnerOperation -OperationId 'docker_context_parse'
    try {
        # Windows PowerShell emits the JSON array as one pipeline item. Assign
        # first, then normalize; @($json | ConvertFrom-Json) would nest arrays.
        # Check the root token too so a bare object is not accepted.
        if (-not $contextJson.TrimStart().StartsWith('[')) { throw 'E2E_INVALID_JSON_SHAPE' }
        $parsedContext = ConvertFrom-Json -InputObject $contextJson -ErrorAction Stop
        $context = @($parsedContext)
        if ($context.Count -ne 1 -or $context[0] -is [array] -or $context[0] -isnot [pscustomobject]) {
            throw 'E2E_INVALID_JSON_SHAPE'
        }
        # Read properties directly, not through a pipeline-returning helper:
        # otherwise a singleton array could be unwrapped into an accepted scalar.
        $inspectedName = $context[0].PSObject.Properties['Name'].Value
        $endpoints = $context[0].PSObject.Properties['Endpoints'].Value
        if ($endpoints -is [array] -or $endpoints -isnot [pscustomobject]) { throw 'E2E_INVALID_JSON_SHAPE' }
        $docker = $endpoints.PSObject.Properties['docker'].Value
        if ($docker -is [array] -or $docker -isnot [pscustomobject]) { throw 'E2E_INVALID_JSON_SHAPE' }
        $endpoint = $docker.PSObject.Properties['Host'].Value
        if ($inspectedName -isnot [string] -or $inspectedName -cne $contextName -or
            $endpoint -isnot [string] -or [string]::IsNullOrWhiteSpace($endpoint)) {
            throw 'E2E_INVALID_JSON_SHAPE'
        }
    }
    catch {
        Stop-E2eDockerContextValidation -Category 'invalid_output'
    }

    Set-E2eRunnerOperation -OperationId 'docker_context_policy_validation'
    if (-not (Test-E2eLocalDockerEndpoint -Endpoint $endpoint -WindowsHost $isWindows) -or
        (-not [string]::IsNullOrWhiteSpace($dockerHost) -and $dockerHost -cne $endpoint)) {
        Stop-E2eDockerContextValidation -Category 'policy_rejected'
    }
    # A local pipe alone does not prove the Linux engine required by this demo.
    # This read-only query runs only after the endpoint has passed local policy.
    $engine = @(Invoke-E2eDockerContextRead -Arguments @('--context', $contextName, 'info', '--format', '{{.OSType}}') `
        -OperationId 'docker_context_policy_validation' -ResolvedExecutable $application)
    if ($engine.Count -eq 0 -or ($engine.Count -eq 1 -and
        $engine[0] -is [string] -and [string]::IsNullOrWhiteSpace($engine[0]))) {
        Stop-E2eDockerContextValidation -Category 'empty_output' -ProcessStarted $true -ExitCode 0
    }
    if ($engine.Count -ne 1 -or $engine[0] -isnot [string] -or $engine[0] -cnotin @('linux', 'windows')) {
        Stop-E2eDockerContextValidation -Category 'invalid_output' -ProcessStarted $true -ExitCode 0
    }
    if ($engine[0] -cne 'linux') {
        Stop-E2eDockerContextValidation -Category 'policy_rejected' -ProcessStarted $true -ExitCode 0
    }
    return $contextName
}

function Enter-E2eRunMutex {
    param([string] $Name = 'Local\ReawoteDemoE2E-v1')

    $mutex = [System.Threading.Mutex]::new($false, $Name)
    try {
        try {
            $acquired = $mutex.WaitOne(0)
        }
        catch [System.Threading.AbandonedMutexException] {
            $acquired = $true
        }
        if (-not $acquired) {
            throw "Another disposable demo E2E run is already active; no Docker mutation was attempted."
        }
        return $mutex
    }
    catch {
        $mutex.Dispose()
        throw
    }
}

function Exit-E2eRunMutex {
    param([Parameter(Mandatory)] [System.Threading.Mutex] $Mutex)

    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}
