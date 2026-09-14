$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'e2e-resource-policy.ps1')

$script:passed = 0

function Assert-PolicyCondition {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Label
    )

    if (-not $Condition) {
        throw "E2E resource policy regression failed: $Label"
    }
    $script:passed++
}

function Assert-PolicyRejected {
    param(
        [Parameter(Mandatory)] [scriptblock] $Action,
        [Parameter(Mandatory)] [string] $Label,
        [string[]] $ForbiddenText = @()
    )

    $output = [Collections.Generic.List[object]]::new()
    $message = $null
    try {
        @(& $Action) | ForEach-Object { [void]$output.Add($_) }
    }
    catch {
        $message = $_.Exception.Message
    }

    if ($output.Count -ne 0) {
        throw "E2E resource policy regression failed: $Label emitted output before rejection"
    }
    if ($message -cne 'E2E_SAFE_RESOURCE_REJECTED') {
        throw "E2E resource policy regression failed: $Label did not use the fixed rejection"
    }
    foreach ($forbidden in $ForbiddenText) {
        if (-not [string]::IsNullOrEmpty($forbidden) -and $message.Contains($forbidden)) {
            throw "E2E resource policy regression failed: $Label exposed rejected input"
        }
    }
    $script:passed++
}

function ConvertTo-PolicyInspectionJson {
    param([Parameter(Mandatory)] $Value)

    return ConvertTo-Json -InputObject ([object[]]@($Value)) -Depth 20 -Compress
}

function Copy-PolicyInspection {
    param([Parameter(Mandatory)] $Value)

    return ConvertFrom-E2eInspectionJson -Json (ConvertTo-PolicyInspectionJson $Value)
}

function New-PolicyContainerInspection {
    param(
        [Parameter(Mandatory)] [string] $Service,
        [Parameter(Mandatory)] [string] $Id,
        [string] $Source = '',
        [switch] $LegacyName
    )

    $name = "/reawote-e2e-$Service-1"
    if ($LegacyName) {
        $name = "/reawote-e2e_$($Service)_1"
    }

    $mounts = [object[]]@()
    if ($Service -ceq 'database') {
        $mounts = [object[]]@(
            [pscustomobject][ordered]@{
                Type = 'volume'
                Name = 'reawote-e2e-postgres-data'
                Driver = 'local'
                Destination = '/var/lib/postgresql'
                RW = $true
            }
        )
    }
    elseif ($Service -ceq 'worker') {
        $mounts = [object[]]@(
            [pscustomobject][ordered]@{
                Type = 'bind'
                Source = $Source
                Destination = '/e2e-materials'
                RW = $false
            }
        )
    }

    return [pscustomobject][ordered]@{
        Id = $Id
        Name = $name
        Config = [pscustomobject][ordered]@{
            Labels = [pscustomobject][ordered]@{
                'com.docker.compose.project' = 'reawote-e2e'
                'com.docker.compose.service' = $Service
                'com.docker.compose.container-number' = '1'
                'com.docker.compose.oneoff' = 'False'
            }
        }
        Mounts = $mounts
    }
}

function New-PolicyNetworkInspection {
    param(
        [Parameter(Mandatory)] [string] $Id,
        [object[]] $AttachedIds = @(),
        $Options = $null
    )

    $containers = [ordered]@{}
    foreach ($attachedId in $AttachedIds) {
        $containers[$attachedId] = [pscustomobject]@{}
    }
    return [pscustomobject][ordered]@{
        Id = $Id
        Name = 'reawote-e2e_default'
        Driver = 'bridge'
        Scope = 'local'
        Labels = [pscustomobject][ordered]@{
            'com.docker.compose.project' = 'reawote-e2e'
            'com.docker.compose.network' = 'default'
        }
        Options = $Options
        Containers = [pscustomobject]$containers
    }
}

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
$repositoryWithSlashes = $repositoryRoot.Replace('\', '/').TrimEnd('/')
$runId = '11111111-1111-4111-8111-111111111111'
$workerSuffix = "/.e2e-data/runs/$runId/materials"
$workerSource = $repositoryWithSlashes + $workerSuffix
$expectedWorkerPath = [IO.Path]::GetFullPath((Join-Path $repositoryRoot (
    '.e2e-data{0}runs{0}{1}{0}materials' -f [IO.Path]::DirectorySeparatorChar, $runId
)))

$databaseId = 'a' * 64
$backendId = 'b' * 64
$workerId = 'c' * 64
$frontendId = 'd' * 64
$networkId = 'f' * 64
$containerIds = [object[]]@($databaseId, $backendId, $workerId, $frontendId)

# Inspection JSON must retain and enforce the Docker CLI root-array shape.
$jsonInspection = New-PolicyContainerInspection -Service 'backend' -Id $backendId
$parsedInspection = ConvertFrom-E2eInspectionJson -Json (ConvertTo-PolicyInspectionJson $jsonInspection)
Assert-PolicyCondition `
    -Condition ($parsedInspection -is [pscustomobject] -and $parsedInspection.Id -ceq $backendId) `
    -Label 'single inspection array is accepted'

$badJsonCases = @(
    @{ Label = 'root object'; Json = (ConvertTo-Json -InputObject $jsonInspection -Depth 20 -Compress) },
    @{ Label = 'empty root array'; Json = '[]' },
    @{ Label = 'multiple root objects'; Json = '[{},{}]' },
    @{ Label = 'nested root array'; Json = '[[{}]]' },
    @{ Label = 'null array item'; Json = '[null]' },
    @{ Label = 'scalar root'; Json = '"inspection"' },
    @{ Label = 'invalid JSON'; Json = '[{"secret":"DO_NOT_EXPOSE"}' }
)
foreach ($case in $badJsonCases) {
    $json = $case.Json
    $action = { ConvertFrom-E2eInspectionJson -Json $json }.GetNewClosure()
    Assert-PolicyRejected -Action $action -Label $case.Label -ForbiddenText @('DO_NOT_EXPOSE')
}
Assert-PolicyRejected `
    -Action { ConvertFrom-E2eInspectionJson -Json $null } `
    -Label 'null JSON uses the fixed rejection'
Assert-PolicyRejected `
    -Action { ConvertFrom-E2eInspectionJson -Json '   ' } `
    -Label 'blank JSON uses the fixed rejection'
$nonStringJson = [object[]]@('[{}]', '[{}]')
Assert-PolicyRejected `
    -Action { ConvertFrom-E2eInspectionJson -Json $nonStringJson }.GetNewClosure() `
    -Label 'JSON array parameter is not coerced to text'

# Every expected service, both supported Compose naming styles, and case-insensitive
# one-off false are accepted. Callbacks are ordered and cannot emit public output.
$validServices = @(
    @{ Service = 'database'; Id = $databaseId },
    @{ Service = 'backend'; Id = $backendId },
    @{ Service = 'worker'; Id = $workerId },
    @{ Service = 'frontend'; Id = $frontendId }
)
foreach ($case in $validServices) {
    $inspection = Copy-PolicyInspection (
        New-PolicyContainerInspection `
            -Service $case.Service `
            -Id $case.Id `
            -Source $workerSource
    )
    $checks = [Collections.Generic.List[string]]::new()
    $result = @(
        Assert-E2eOwnedContainer `
            -Inspection $inspection `
            -ExpectedId $case.Id `
            -RepositoryRoot $repositoryRoot `
            -OnCheck {
                param($check)
                [void]$checks.Add($check)
                Write-Output 'callback output must stay private'
            }
    )
    Assert-PolicyCondition `
        -Condition ($result.Count -eq 1 -and $result[0] -ceq $case.Service) `
        -Label "$($case.Service) container is accepted"
    Assert-PolicyCondition `
        -Condition ($checks.Count -eq 2 -and
            $checks[0] -ceq 'container_ownership_validation' -and
            $checks[1] -ceq 'container_mount_validation') `
        -Label "$($case.Service) callback order is stable"
}

$legacy = New-PolicyContainerInspection `
    -Service 'database' `
    -Id $databaseId `
    -LegacyName
$legacy.Config.Labels.PSObject.Properties['com.docker.compose.oneoff'].Value = 'false'
Assert-PolicyCondition `
    -Condition ((Assert-E2eOwnedContainer `
        -Inspection $legacy `
        -ExpectedId $databaseId `
        -RepositoryRoot $repositoryRoot) -ceq 'database') `
    -Label 'legacy Compose name and lowercase false are accepted'

$resolvedSource = Resolve-E2eOwnedWorkerSource `
    -Source $workerSource `
    -RepositoryRoot $repositoryRoot
Assert-PolicyCondition `
    -Condition ($resolvedSource.Equals($expectedWorkerPath, [StringComparison]::OrdinalIgnoreCase)) `
    -Label 'worker source resolves to the native run materials path'
Assert-PolicyRejected `
    -Action { Resolve-E2eOwnedWorkerSource -Source $null -RepositoryRoot $repositoryRoot }.GetNewClosure() `
    -Label 'null worker source uses the fixed rejection'

if ($repositoryWithSlashes -cmatch '\A(?<drive>[A-Za-z]):(?<tail>/.*)\z') {
    $drive = $matches['drive'].ToLowerInvariant()
    $tail = $matches['tail']
    $desktopSources = @(
        ($workerSource.Replace('/', '\')),
        "/host_mnt/$drive$tail$workerSuffix",
        "/run/desktop/mnt/host/$drive$tail$workerSuffix"
    )
    foreach ($desktopSource in $desktopSources) {
        $resolved = Resolve-E2eOwnedWorkerSource `
            -Source $desktopSource `
            -RepositoryRoot $repositoryRoot
        Assert-PolicyCondition `
            -Condition ($resolved.Equals($expectedWorkerPath, [StringComparison]::OrdinalIgnoreCase)) `
            -Label 'Docker Desktop worker source maps to the expected drive'
    }
}

$secret = 'PRODUCTION-NAS-CREDENTIAL-DO-NOT-PRINT'
$containerCases = @(
    @{ Label = 'inspection array'; Mutate = { param($value) return [object[]]@($value, $value) } },
    @{ Label = 'ID property array'; Mutate = { param($value) $value.Id = [object[]]@($value.Id); return $value } },
    @{ Label = 'Config array'; Mutate = { param($value) $value.Config = [object[]]@($value.Config); return $value } },
    @{ Label = 'Labels hashtable'; Mutate = { param($value) $value.Config.Labels = [ordered]@{}; return $value } },
    @{ Label = 'missing project label'; Mutate = {
        param($value)
        [void]$value.Config.Labels.PSObject.Properties.Remove('com.docker.compose.project')
        return $value
    } },
    @{ Label = 'foreign project label'; Mutate = {
        param($value)
        $value.Config.Labels.PSObject.Properties['com.docker.compose.project'].Value = $secret
        return $value
    } },
    @{ Label = 'service label array'; Mutate = {
        param($value)
        $value.Config.Labels.PSObject.Properties['com.docker.compose.service'].Value = [object[]]@('backend')
        return $value
    } },
    @{ Label = 'unknown service'; Mutate = {
        param($value)
        $value.Config.Labels.PSObject.Properties['com.docker.compose.service'].Value = 'api'
        return $value
    } },
    @{ Label = 'container number integer'; Mutate = {
        param($value)
        $value.Config.Labels.PSObject.Properties['com.docker.compose.container-number'].Value = 1
        return $value
    } },
    @{ Label = 'oneoff boolean'; Mutate = {
        param($value)
        $value.Config.Labels.PSObject.Properties['com.docker.compose.oneoff'].Value = $false
        return $value
    } },
    @{ Label = 'foreign similar-prefix name'; Mutate = {
        param($value)
        $value.Name = '/reawote-e2e-backend-1-foreign'
        return $value
    } },
    @{ Label = 'Mounts scalar'; Mutate = {
        param($value)
        $value.Mounts = [pscustomobject]@{}
        return $value
    } },
    @{ Label = 'unexpected backend mount'; Mutate = {
        param($value)
        $value.Mounts = [object[]]@([pscustomobject]@{
            Type = 'bind'; Source = $workerSource; Destination = '/e2e-materials'; RW = $false
        })
        return $value
    } }
)
foreach ($case in $containerCases) {
    $copy = Copy-PolicyInspection (
        New-PolicyContainerInspection -Service 'backend' -Id $backendId
    )
    $mutator = $case.Mutate
    $invalid = & $mutator $copy
    $action = {
        Assert-E2eOwnedContainer `
            -Inspection $invalid `
            -ExpectedId $backendId `
            -RepositoryRoot $repositoryRoot
    }.GetNewClosure()
    Assert-PolicyRejected `
        -Action $action `
        -Label $case.Label `
        -ForbiddenText @($secret, $workerSource)
}

$validBackend = New-PolicyContainerInspection -Service 'backend' -Id $backendId
Assert-PolicyRejected `
    -Action { Assert-E2eOwnedContainer } `
    -Label 'missing container arguments use the fixed rejection'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedContainer `
            -Inspection (, $validBackend) `
            -ExpectedId $backendId `
            -RepositoryRoot $repositoryRoot
    }.GetNewClosure() `
    -Label 'one-item container inspection array is not unwrapped'
$expectedIdArray = [object[]]@($backendId)
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedContainer `
            -Inspection $validBackend `
            -ExpectedId $expectedIdArray `
            -RepositoryRoot $repositoryRoot
    }.GetNewClosure() `
    -Label 'expected container ID array is not coerced'

$databaseCases = @(
    @{ Label = 'database has no mount'; Mutate = { param($value) $value.Mounts = [object[]]@(); return $value } },
    @{ Label = 'database mount type'; Mutate = { param($value) $value.Mounts[0].Type = 'bind'; return $value } },
    @{ Label = 'database volume name'; Mutate = { param($value) $value.Mounts[0].Name = 'foreign-volume'; return $value } },
    @{ Label = 'database volume driver'; Mutate = { param($value) $value.Mounts[0].Driver = 'nfs'; return $value } },
    @{ Label = 'database destination'; Mutate = { param($value) $value.Mounts[0].Destination = '/data'; return $value } },
    @{ Label = 'database RW string'; Mutate = { param($value) $value.Mounts[0].RW = 'true'; return $value } },
    @{ Label = 'database extra mount'; Mutate = {
        param($value)
        $value.Mounts = [object[]]@($value.Mounts[0], (Copy-PolicyInspection $value.Mounts[0]))
        return $value
    } }
)
foreach ($case in $databaseCases) {
    $invalid = Copy-PolicyInspection (
        New-PolicyContainerInspection -Service 'database' -Id $databaseId
    )
    $mutator = $case.Mutate
    $invalid = & $mutator $invalid
    $action = {
        Assert-E2eOwnedContainer `
            -Inspection $invalid `
            -ExpectedId $databaseId `
            -RepositoryRoot $repositoryRoot
    }.GetNewClosure()
    Assert-PolicyRejected -Action $action -Label $case.Label
}

$workerCases = @(
    @{ Label = 'worker mount type'; Source = $workerSource; Mutate = { param($value) $value.Mounts[0].Type = 'volume' } },
    @{ Label = 'worker destination'; Source = $workerSource; Mutate = { param($value) $value.Mounts[0].Destination = '/materials' } },
    @{ Label = 'worker RW string'; Source = $workerSource; Mutate = { param($value) $value.Mounts[0].RW = 'false' } },
    @{ Label = 'worker source array'; Source = $workerSource; Mutate = { param($value) $value.Mounts[0].Source = [object[]]@($value.Mounts[0].Source) } },
    @{ Label = 'worker similar-prefix repository'; Source = ($repositoryWithSlashes + '-foreign' + $workerSuffix); Mutate = { param($value) } },
    @{ Label = 'worker traversal'; Source = ($repositoryWithSlashes + "/.e2e-data/runs/$runId/../$runId/materials"); Mutate = { param($value) } },
    @{ Label = 'worker empty GUID'; Source = ($repositoryWithSlashes + '/.e2e-data/runs/00000000-0000-0000-0000-000000000000/materials'); Mutate = { param($value) } },
    @{ Label = 'worker malformed GUID'; Source = ($repositoryWithSlashes + '/.e2e-data/runs/not-a-guid/materials'); Mutate = { param($value) } },
    @{ Label = 'worker extra path'; Source = ($workerSource + '/nested'); Mutate = { param($value) } },
    @{ Label = 'worker extra mount'; Source = $workerSource; Mutate = {
        param($value)
        $value.Mounts = [object[]]@($value.Mounts[0], (Copy-PolicyInspection $value.Mounts[0]))
    } }
)
foreach ($case in $workerCases) {
    $invalid = New-PolicyContainerInspection `
        -Service 'worker' `
        -Id $workerId `
        -Source $case.Source
    $mutator = $case.Mutate
    & $mutator $invalid
    $action = {
        Assert-E2eOwnedContainer `
            -Inspection $invalid `
            -ExpectedId $workerId `
            -RepositoryRoot $repositoryRoot
    }.GetNewClosure()
    Assert-PolicyRejected `
        -Action $action `
        -Label $case.Label `
        -ForbiddenText @($case.Source)
}

if ($repositoryWithSlashes -cmatch '\A(?<drive>[A-Za-z]):(?<tail>/.*)\z') {
    $wrongDrive = if ($matches['drive'] -ceq 'Z') { 'y' } else { 'z' }
    $wrongDriveSource = "/host_mnt/$wrongDrive$($matches['tail'])$workerSuffix"
    Assert-PolicyRejected `
        -Action {
            Resolve-E2eOwnedWorkerSource `
                -Source $wrongDriveSource `
                -RepositoryRoot $repositoryRoot
        }.GetNewClosure() `
        -Label 'Docker Desktop source cannot map another drive' `
        -ForbiddenText @($wrongDriveSource)

    $mixedSource = $workerSource.Replace('/.e2e-data', '\.e2e-data')
    Assert-PolicyRejected `
        -Action {
            Resolve-E2eOwnedWorkerSource `
                -Source $mixedSource `
                -RepositoryRoot $repositoryRoot
        }.GetNewClosure() `
        -Label 'mixed native source separators are rejected' `
        -ForbiddenText @($mixedSource)

    $uncRoot = '\\server\share\repository'
    Assert-PolicyRejected `
        -Action {
            Resolve-E2eOwnedWorkerSource `
                -Source "//server/share/repository$workerSuffix" `
                -RepositoryRoot $uncRoot
        }.GetNewClosure() `
        -Label 'UNC repository roots are rejected' `
        -ForbiddenText @($uncRoot)
}

# A failed ownership check reports only the ownership phase. A failed mount check
# reports both phase names, which lets the runner record a precise operation.
$ownershipChecks = [Collections.Generic.List[string]]::new()
$foreignContainer = New-PolicyContainerInspection -Service 'backend' -Id $backendId
$foreignContainer.Config.Labels.PSObject.Properties['com.docker.compose.project'].Value = 'foreign'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedContainer `
            -Inspection $foreignContainer `
            -ExpectedId $backendId `
            -RepositoryRoot $repositoryRoot `
            -OnCheck { param($check) [void]$ownershipChecks.Add($check) }
    }.GetNewClosure() `
    -Label 'container ownership callback rejection'
Assert-PolicyCondition `
    -Condition ($ownershipChecks.Count -eq 1 -and
        $ownershipChecks[0] -ceq 'container_ownership_validation') `
    -Label 'container ownership failure retains its operation'

$mountChecks = [Collections.Generic.List[string]]::new()
$badMountContainer = New-PolicyContainerInspection -Service 'backend' -Id $backendId
$badMountContainer.Mounts = [pscustomobject]@{}
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedContainer `
            -Inspection $badMountContainer `
            -ExpectedId $backendId `
            -RepositoryRoot $repositoryRoot `
            -OnCheck { param($check) [void]$mountChecks.Add($check) }
    }.GetNewClosure() `
    -Label 'container mount callback rejection'
Assert-PolicyCondition `
    -Condition ($mountChecks.Count -eq 2 -and
        $mountChecks[1] -ceq 'container_mount_validation') `
    -Label 'container mount failure retains its operation'

$validNetwork = Copy-PolicyInspection (
    New-PolicyNetworkInspection `
        -Id $networkId `
        -AttachedIds ([object[]]@($databaseId, $workerId)) `
        -Options ([pscustomobject]@{})
)
$networkChecks = [Collections.Generic.List[string]]::new()
$networkResult = @(
    Assert-E2eOwnedNetwork `
        -Inspection $validNetwork `
        -ExpectedId $networkId `
        -ContainerIds $containerIds `
        -OnCheck {
            param($check)
            [void]$networkChecks.Add($check)
            Write-Output 'callback output must stay private'
        }
)
Assert-PolicyCondition `
    -Condition ($networkResult.Count -eq 1 -and $networkResult[0] -ceq 'reawote-e2e_default') `
    -Label 'owned network is accepted'
Assert-PolicyCondition `
    -Condition ($networkChecks.Count -eq 2 -and
        $networkChecks[0] -ceq 'network_ownership_validation' -and
        $networkChecks[1] -ceq 'network_attachment_validation') `
    -Label 'network callback order is stable'

$emptyNetwork = New-PolicyNetworkInspection -Id $networkId -AttachedIds @() -Options $null
Assert-PolicyCondition `
    -Condition ((Assert-E2eOwnedNetwork `
        -Inspection $emptyNetwork `
        -ExpectedId $networkId `
        -ContainerIds ([object[]]@())) -ceq 'reawote-e2e_default') `
    -Label 'empty owned network with null options is accepted'

$networkCases = @(
    @{ Label = 'network inspection array'; Mutate = { param($value) return [object[]]@($value, $value) } },
    @{ Label = 'network ID array'; Mutate = { param($value) $value.Id = [object[]]@($value.Id); return $value } },
    @{ Label = 'foreign network name'; Mutate = { param($value) $value.Name = 'reawote-e2e_default-foreign'; return $value } },
    @{ Label = 'network driver'; Mutate = { param($value) $value.Driver = 'overlay'; return $value } },
    @{ Label = 'network scope'; Mutate = { param($value) $value.Scope = 'swarm'; return $value } },
    @{ Label = 'network Labels array'; Mutate = { param($value) $value.Labels = [object[]]@($value.Labels); return $value } },
    @{ Label = 'missing network project label'; Mutate = {
        param($value)
        [void]$value.Labels.PSObject.Properties.Remove('com.docker.compose.project')
        return $value
    } },
    @{ Label = 'foreign network project'; Mutate = {
        param($value)
        $value.Labels.PSObject.Properties['com.docker.compose.project'].Value = $secret
        return $value
    } },
    @{ Label = 'foreign Compose network label'; Mutate = {
        param($value)
        $value.Labels.PSObject.Properties['com.docker.compose.network'].Value = 'other'
        return $value
    } },
    @{ Label = 'network Options array'; Mutate = { param($value) $value.Options = [object[]]@(); return $value } },
    @{ Label = 'network Options nonempty'; Mutate = { param($value) $value.Options = [pscustomobject]@{ mtu = '1500' }; return $value } },
    @{ Label = 'network Containers array'; Mutate = { param($value) $value.Containers = [object[]]@($value.Containers); return $value } },
    @{ Label = 'foreign network attachment'; Mutate = {
        param($value)
        Add-Member -InputObject $value.Containers -MemberType NoteProperty -Name ('e' * 64) -Value ([pscustomobject]@{})
        return $value
    } },
    @{ Label = 'malformed attachment ID'; Mutate = {
        param($value)
        Add-Member -InputObject $value.Containers -MemberType NoteProperty -Name 'short-id' -Value ([pscustomobject]@{})
        return $value
    } },
    @{ Label = 'attachment value scalar'; Mutate = {
        param($value)
        $value.Containers.PSObject.Properties[$databaseId].Value = 'not-an-inspection-object'
        return $value
    } }
)
foreach ($case in $networkCases) {
    $invalid = Copy-PolicyInspection $validNetwork
    $mutator = $case.Mutate
    $invalid = & $mutator $invalid
    $action = {
        Assert-E2eOwnedNetwork `
            -Inspection $invalid `
            -ExpectedId $networkId `
            -ContainerIds $containerIds
    }.GetNewClosure()
    Assert-PolicyRejected `
        -Action $action `
        -Label $case.Label `
        -ForbiddenText @($secret)
}

$knownIdArray = [object[]]@($databaseId)
Assert-PolicyRejected `
    -Action { Assert-E2eOwnedNetwork } `
    -Label 'missing network arguments use the fixed rejection'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection (, $validNetwork) `
            -ExpectedId $networkId `
            -ContainerIds $containerIds
    }.GetNewClosure() `
    -Label 'one-item network inspection array is not unwrapped'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection $validNetwork `
            -ExpectedId ([object[]]@($networkId)) `
            -ContainerIds $knownIdArray
    }.GetNewClosure() `
    -Label 'expected network ID array is not coerced'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection $validNetwork `
            -ExpectedId $networkId `
            -ContainerIds $databaseId
    }.GetNewClosure() `
    -Label 'container ID scalar is not coerced to an array'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection $validNetwork `
            -ExpectedId $networkId `
            -ContainerIds ([object[]]@($databaseId, $databaseId))
    }.GetNewClosure() `
    -Label 'duplicate known container IDs are rejected'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection $validNetwork `
            -ExpectedId $networkId `
            -ContainerIds ([object[]]@(1, $workerId))
    }.GetNewClosure() `
    -Label 'numeric known container ID is rejected'

$networkOwnershipChecks = [Collections.Generic.List[string]]::new()
$badNetworkOwner = Copy-PolicyInspection $validNetwork
$badNetworkOwner.Labels.PSObject.Properties['com.docker.compose.project'].Value = 'foreign'
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection $badNetworkOwner `
            -ExpectedId $networkId `
            -ContainerIds $containerIds `
            -OnCheck { param($check) [void]$networkOwnershipChecks.Add($check) }
    }.GetNewClosure() `
    -Label 'network ownership callback rejection'
Assert-PolicyCondition `
    -Condition ($networkOwnershipChecks.Count -eq 1 -and
        $networkOwnershipChecks[0] -ceq 'network_ownership_validation') `
    -Label 'network ownership failure retains its operation'

$networkAttachmentChecks = [Collections.Generic.List[string]]::new()
$badNetworkAttachment = Copy-PolicyInspection $validNetwork
Add-Member `
    -InputObject $badNetworkAttachment.Containers `
    -MemberType NoteProperty `
    -Name ('e' * 64) `
    -Value ([pscustomobject]@{})
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedNetwork `
            -Inspection $badNetworkAttachment `
            -ExpectedId $networkId `
            -ContainerIds $containerIds `
            -OnCheck { param($check) [void]$networkAttachmentChecks.Add($check) }
    }.GetNewClosure() `
    -Label 'network attachment callback rejection'
Assert-PolicyCondition `
    -Condition ($networkAttachmentChecks.Count -eq 2 -and
        $networkAttachmentChecks[1] -ceq 'network_attachment_validation') `
    -Label 'network attachment failure retains its operation'

Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedContainer `
            -Inspection $validBackend `
            -ExpectedId $backendId `
            -RepositoryRoot $repositoryRoot `
            -OnCheck { throw $secret }
    }.GetNewClosure() `
    -Label 'callback errors are sanitized' `
    -ForbiddenText @($secret)
Assert-PolicyRejected `
    -Action {
        Assert-E2eOwnedContainer `
            -Inspection $validBackend `
            -ExpectedId $backendId `
            -RepositoryRoot $repositoryRoot `
            -OnCheck 'not a scriptblock'
    }.GetNewClosure() `
    -Label 'callback type is exact'

Write-Host "E2E resource policy regressions: $script:passed passed, 0 skipped, 0 failed."
