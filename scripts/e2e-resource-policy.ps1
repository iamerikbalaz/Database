$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Stop-E2eResourcePolicy {
    throw 'E2E_SAFE_RESOURCE_REJECTED'
}

function Test-E2eResourceNoteProperty {
    param($Object, $Name)

    if ($Object -isnot [pscustomobject] -or $Name -isnot [string]) { return $false }
    $property = $Object.PSObject.Properties[$Name]
    return ($null -ne $property -and $property.MemberType -eq 'NoteProperty')
}

function Test-E2eResourceString {
    param($Value, [string] $Expected)

    return ($Value -is [string] -and $Value.Equals($Expected, [StringComparison]::Ordinal))
}

function Test-E2eResourceId {
    param($Value)

    return ($Value -is [string] -and $Value -cmatch '\A[0-9a-f]{64}\z')
}

function Invoke-E2eResourcePolicyCheck {
    param($OnCheck, [string] $Name)

    if ($null -eq $OnCheck) { return }
    if ($OnCheck -isnot [scriptblock]) { Stop-E2eResourcePolicy }
    & $OnCheck $Name 2>$null 3>$null 4>$null 5>$null 6>$null | Out-Null
}

function Resolve-E2eOwnedWorkerSource {
    param(
        $Source,
        $RepositoryRoot
    )

    try {
        if ($Source -isnot [string] -or $RepositoryRoot -isnot [string] -or
            [string]::IsNullOrWhiteSpace($Source) -or
            [string]::IsNullOrWhiteSpace($RepositoryRoot)) {
            Stop-E2eResourcePolicy
        }

        $repository = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if (-not [IO.Path]::IsPathRooted($repository)) { Stop-E2eResourcePolicy }

        $windowsPath = $repository -cmatch '\A[A-Za-z]:[\\/]'
        if ($windowsPath) {
            $provided = $RepositoryRoot.Replace('/', '\').TrimEnd('\')
            if (-not $provided.Equals($repository, [StringComparison]::OrdinalIgnoreCase)) {
                Stop-E2eResourcePolicy
            }
        }
        else {
            if ([IO.Path]::DirectorySeparatorChar -eq '\' -or
                -not $repository.StartsWith('/', [StringComparison]::Ordinal) -or
                $repository.Contains('\') -or
                -not $RepositoryRoot.TrimEnd('/').Equals($repository, [StringComparison]::Ordinal)) {
                Stop-E2eResourcePolicy
            }
        }

        $repositoryWithSlashes = $repository.Replace('\', '/').TrimEnd('/')
        $sourceForMatch = $Source
        $allowedRoots = [Collections.Generic.List[string]]::new()
        [void]$allowedRoots.Add($repositoryWithSlashes)
        $comparison = [StringComparison]::Ordinal
        $regexOptions = [Text.RegularExpressions.RegexOptions]::CultureInvariant

        if ($windowsPath) {
            $comparison = [StringComparison]::OrdinalIgnoreCase
            $regexOptions = $regexOptions -bor [Text.RegularExpressions.RegexOptions]::IgnoreCase
            if ($Source -cmatch '\A[A-Za-z]:\\') {
                if ($Source.Contains('/')) { Stop-E2eResourcePolicy }
                $sourceForMatch = $Source.Replace('\', '/')
            }
            elseif ($Source -cmatch '\A[A-Za-z]:/' -and $Source.Contains('\')) {
                Stop-E2eResourcePolicy
            }
            $drive = $repositoryWithSlashes.Substring(0, 1).ToLowerInvariant()
            $driveRelative = $repositoryWithSlashes.Substring(2)
            [void]$allowedRoots.Add("/host_mnt/$drive$driveRelative")
            [void]$allowedRoots.Add("/run/desktop/mnt/host/$drive$driveRelative")
        }

        foreach ($allowedRoot in $allowedRoots) {
            $prefix = $allowedRoot + '/.e2e-data/runs/'
            if (-not $sourceForMatch.StartsWith($prefix, $comparison)) { continue }
            $remainder = $sourceForMatch.Substring($prefix.Length)
            $match = [regex]::Match(
                $remainder,
                '\A(?<guid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/materials\z',
                $regexOptions
            )
            if (-not $match.Success) { continue }
            $runGuid = [guid]::Empty
            if (-not [guid]::TryParseExact($match.Groups['guid'].Value, 'D', [ref]$runGuid) -or
                $runGuid -eq [guid]::Empty) {
                continue
            }
            $nativePath = Join-Path -Path $repository -ChildPath (
                '.e2e-data{0}runs{0}{1}{0}materials' -f
                [IO.Path]::DirectorySeparatorChar,
                $runGuid.ToString('D')
            )
            return [IO.Path]::GetFullPath($nativePath)
        }
        Stop-E2eResourcePolicy
    }
    catch {
        Stop-E2eResourcePolicy
    }
}

function ConvertFrom-E2eInspectionJson {
    param($Json)

    try {
        if ($Json -isnot [string] -or [string]::IsNullOrWhiteSpace($Json)) {
            Stop-E2eResourcePolicy
        }
        # Preserve the JSON root type. Wrapping the pipeline in @() first would
        # make a root object indistinguishable from a one-item root array.
        $parsed = ConvertFrom-Json -InputObject $Json -ErrorAction Stop
        if ($parsed -isnot [array] -or $parsed.Count -ne 1 -or
            $parsed[0] -isnot [pscustomobject]) {
            Stop-E2eResourcePolicy
        }
        return $parsed[0]
    }
    catch {
        Stop-E2eResourcePolicy
    }
}

function Assert-E2eOwnedContainer {
    param(
        $Inspection,
        $ExpectedId,
        $RepositoryRoot,
        $OnCheck = $null
    )

    try {
        if ($null -ne $OnCheck -and $OnCheck -isnot [scriptblock]) {
            Stop-E2eResourcePolicy
        }
        [void](Invoke-E2eResourcePolicyCheck -OnCheck $OnCheck -Name 'container_ownership_validation')

        if ($Inspection -isnot [pscustomobject] -or
            -not (Test-E2eResourceId $ExpectedId) -or
            $RepositoryRoot -isnot [string] -or
            [string]::IsNullOrWhiteSpace($RepositoryRoot)) {
            Stop-E2eResourcePolicy
        }
        foreach ($name in @('Id', 'Name', 'Config')) {
            if (-not (Test-E2eResourceNoteProperty $Inspection $name)) {
                Stop-E2eResourcePolicy
            }
        }
        $inspectionId = $Inspection.PSObject.Properties['Id'].Value
        $containerName = $Inspection.PSObject.Properties['Name'].Value
        $config = $Inspection.PSObject.Properties['Config'].Value
        if (-not (Test-E2eResourceString $inspectionId $ExpectedId) -or
            $containerName -isnot [string] -or $config -isnot [pscustomobject] -or
            -not (Test-E2eResourceNoteProperty $config 'Labels')) {
            Stop-E2eResourcePolicy
        }
        $labels = $config.PSObject.Properties['Labels'].Value
        if ($labels -isnot [pscustomobject]) { Stop-E2eResourcePolicy }
        foreach ($labelName in @(
            'com.docker.compose.project',
            'com.docker.compose.service',
            'com.docker.compose.container-number',
            'com.docker.compose.oneoff'
        )) {
            if (-not (Test-E2eResourceNoteProperty $labels $labelName)) {
                Stop-E2eResourcePolicy
            }
        }
        $project = $labels.PSObject.Properties['com.docker.compose.project'].Value
        $service = $labels.PSObject.Properties['com.docker.compose.service'].Value
        $number = $labels.PSObject.Properties['com.docker.compose.container-number'].Value
        $oneoff = $labels.PSObject.Properties['com.docker.compose.oneoff'].Value
        if (-not (Test-E2eResourceString $project 'reawote-e2e') -or
            $service -isnot [string] -or
            @('database', 'backend', 'worker', 'frontend') -cnotcontains $service -or
            -not (Test-E2eResourceString $number '1') -or
            $oneoff -isnot [string] -or
            -not $oneoff.Equals('False', [StringComparison]::OrdinalIgnoreCase)) {
            Stop-E2eResourcePolicy
        }
        $modernName = "/reawote-e2e-$service-1"
        $legacyName = "/reawote-e2e_$($service)_1"
        if (-not (Test-E2eResourceString $containerName $modernName) -and
            -not (Test-E2eResourceString $containerName $legacyName)) {
            Stop-E2eResourcePolicy
        }

        [void](Invoke-E2eResourcePolicyCheck -OnCheck $OnCheck -Name 'container_mount_validation')
        if (-not (Test-E2eResourceNoteProperty $Inspection 'Mounts')) {
            Stop-E2eResourcePolicy
        }
        # Read this property directly: a PowerShell function would unwrap a
        # singleton array and destroy the Docker inspection shape.
        $mounts = $Inspection.PSObject.Properties['Mounts'].Value
        if ($mounts -isnot [array]) { Stop-E2eResourcePolicy }

        if (@('backend', 'frontend') -ccontains $service) {
            if ($mounts.Count -ne 0) { Stop-E2eResourcePolicy }
            return $service
        }
        if ($mounts.Count -ne 1 -or $mounts[0] -isnot [pscustomobject]) {
            Stop-E2eResourcePolicy
        }
        $mount = $mounts[0]
        if ($service -ceq 'database') {
            foreach ($name in @('Type', 'Name', 'Driver', 'Destination', 'RW')) {
                if (-not (Test-E2eResourceNoteProperty $mount $name)) {
                    Stop-E2eResourcePolicy
                }
            }
            if (-not (Test-E2eResourceString $mount.PSObject.Properties['Type'].Value 'volume') -or
                -not (Test-E2eResourceString $mount.PSObject.Properties['Name'].Value 'reawote-e2e-postgres-data') -or
                -not (Test-E2eResourceString $mount.PSObject.Properties['Driver'].Value 'local') -or
                -not (Test-E2eResourceString $mount.PSObject.Properties['Destination'].Value '/var/lib/postgresql') -or
                $mount.PSObject.Properties['RW'].Value -isnot [bool] -or
                $mount.PSObject.Properties['RW'].Value -ne $true) {
                Stop-E2eResourcePolicy
            }
            return $service
        }

        foreach ($name in @('Type', 'Source', 'Destination', 'RW')) {
            if (-not (Test-E2eResourceNoteProperty $mount $name)) {
                Stop-E2eResourcePolicy
            }
        }
        if (-not (Test-E2eResourceString $mount.PSObject.Properties['Type'].Value 'bind') -or
            -not (Test-E2eResourceString $mount.PSObject.Properties['Destination'].Value '/e2e-materials') -or
            $mount.PSObject.Properties['RW'].Value -isnot [bool] -or
            $mount.PSObject.Properties['RW'].Value -ne $false) {
            Stop-E2eResourcePolicy
        }
        [void](Resolve-E2eOwnedWorkerSource `
            -Source $mount.PSObject.Properties['Source'].Value `
            -RepositoryRoot $RepositoryRoot)
        return $service
    }
    catch {
        Stop-E2eResourcePolicy
    }
}

function Assert-E2eOwnedNetwork {
    param(
        $Inspection,
        $ExpectedId,
        $ContainerIds,
        $OnCheck = $null
    )

    try {
        if ($null -ne $OnCheck -and $OnCheck -isnot [scriptblock]) {
            Stop-E2eResourcePolicy
        }
        [void](Invoke-E2eResourcePolicyCheck -OnCheck $OnCheck -Name 'network_ownership_validation')

        if ($Inspection -isnot [pscustomobject] -or -not (Test-E2eResourceId $ExpectedId)) {
            Stop-E2eResourcePolicy
        }
        foreach ($name in @('Id', 'Name', 'Driver', 'Scope', 'Labels', 'Options')) {
            if (-not (Test-E2eResourceNoteProperty $Inspection $name)) {
                Stop-E2eResourcePolicy
            }
        }
        if (-not (Test-E2eResourceString $Inspection.PSObject.Properties['Id'].Value $ExpectedId) -or
            -not (Test-E2eResourceString $Inspection.PSObject.Properties['Name'].Value 'reawote-e2e_default') -or
            -not (Test-E2eResourceString $Inspection.PSObject.Properties['Driver'].Value 'bridge') -or
            -not (Test-E2eResourceString $Inspection.PSObject.Properties['Scope'].Value 'local')) {
            Stop-E2eResourcePolicy
        }
        $labels = $Inspection.PSObject.Properties['Labels'].Value
        if ($labels -isnot [pscustomobject] -or
            -not (Test-E2eResourceNoteProperty $labels 'com.docker.compose.project') -or
            -not (Test-E2eResourceNoteProperty $labels 'com.docker.compose.network') -or
            -not (Test-E2eResourceString $labels.PSObject.Properties['com.docker.compose.project'].Value 'reawote-e2e') -or
            -not (Test-E2eResourceString $labels.PSObject.Properties['com.docker.compose.network'].Value 'default')) {
            Stop-E2eResourcePolicy
        }
        $options = $Inspection.PSObject.Properties['Options'].Value
        if ($null -ne $options -and
            ($options -isnot [pscustomobject] -or @($options.PSObject.Properties).Count -ne 0)) {
            Stop-E2eResourcePolicy
        }

        [void](Invoke-E2eResourcePolicyCheck -OnCheck $OnCheck -Name 'network_attachment_validation')
        if ($ContainerIds -isnot [array] -or
            -not (Test-E2eResourceNoteProperty $Inspection 'Containers')) {
            Stop-E2eResourcePolicy
        }
        $knownIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($containerId in $ContainerIds) {
            if (-not (Test-E2eResourceId $containerId) -or -not $knownIds.Add($containerId)) {
                Stop-E2eResourcePolicy
            }
        }
        $containers = $Inspection.PSObject.Properties['Containers'].Value
        if ($containers -isnot [pscustomobject]) { Stop-E2eResourcePolicy }
        foreach ($property in $containers.PSObject.Properties) {
            if ($property.MemberType -ne 'NoteProperty' -or
                -not (Test-E2eResourceId $property.Name) -or
                -not $knownIds.Contains($property.Name) -or
                $property.Value -isnot [pscustomobject]) {
                Stop-E2eResourcePolicy
            }
        }
        return 'reawote-e2e_default'
    }
    catch {
        Stop-E2eResourcePolicy
    }
}
