$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

function Assert-Throws {
    param([Parameter(Mandatory)] [scriptblock] $Action, [Parameter(Mandatory)] [string] $Name)
    try { & $Action }
    catch { Write-Host "PASS: $Name"; return }
    throw "Expected failure did not occur: $Name"
}

function Assert-Equal {
    param($Actual, $Expected, [string] $Name)
    if ($Actual -ne $Expected) { throw "$Name expected '$Expected', got '$Actual'." }
    Write-Host "PASS: $Name"
}

function Assert-ThrowsWithMessage {
    param(
        [Parameter(Mandatory)] [scriptblock] $Action,
        [Parameter(Mandatory)] [string[]] $ExpectedFragments,
        [Parameter(Mandatory)] [string] $Name,
        [string[]] $ForbiddenFragments = @()
    )
    try { & $Action }
    catch {
        $message = $_.Exception.Message
        foreach ($fragment in $ExpectedFragments) {
            if (-not $message.Contains($fragment)) {
                throw "$Name expected error fragment '$fragment', got '$message'."
            }
        }
        foreach ($fragment in $ForbiddenFragments) {
            if ($message.Contains($fragment)) {
                throw "$Name exposed forbidden error fragment '$fragment'."
            }
        }
        Write-Host "PASS: $Name"
        return
    }
    throw "Expected failure did not occur: $Name"
}

$repositoryRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runsRoot = Join-Path $repositoryRoot '.e2e-data\runs'
$createdRuns = [Collections.Generic.List[object]]::new()
$junctions = [Collections.Generic.List[string]]::new()
$sentinels = [Collections.Generic.List[string]]::new()

try {
    $safeGuid = [guid]::NewGuid()
    $safeRun = New-E2eManagedRunDirectory $repositoryRoot $runsRoot $safeGuid
    $createdRuns.Add([pscustomobject]@{ Guid = $safeGuid; Path = $safeRun })
    Assert-Equal (Test-E2ePathWithinRoot $runsRoot $safeRun) $true 'safe local run root'

    Assert-Throws { Assert-LocalE2eRepositoryRoot '\\server\share\repo' } 'UNC repository root'
    Assert-Throws { Assert-LocalE2eRepositoryRoot $repositoryRoot ([IO.DriveType]::Network) } 'network drive repository root'

    $prefixGuid = [guid]::NewGuid()
    $similarPrefix = Join-Path (Join-Path $repositoryRoot '.e2e-data\runs-other') $prefixGuid.ToString('D')
    Assert-Throws { Assert-E2eManagedRunPath $repositoryRoot $runsRoot $prefixGuid $similarPrefix } 'similar textual prefix outside runs root'

    $junctionCapability = $true
    $probeTarget = Join-Path $repositoryRoot ".e2e-data\junction-probe-target-$([guid]::NewGuid().ToString('N'))"
    $probeLink = Join-Path $repositoryRoot ".e2e-data\junction-probe-link-$([guid]::NewGuid().ToString('N'))"
    [void][IO.Directory]::CreateDirectory($probeTarget)
    try { [void](New-Item -ItemType Junction -Path $probeLink -Target $probeTarget -ErrorAction Stop) }
    catch { $junctionCapability = $false; Write-Warning "Junction tests skipped because junction creation is unavailable: $($_.Exception.Message)" }
    finally {
        if (Test-Path -LiteralPath $probeLink) { Remove-Item -LiteralPath $probeLink -Force }
        if (Test-Path -LiteralPath $probeTarget) { Remove-Item -LiteralPath $probeTarget -Force }
    }

    if ($junctionCapability) {
        $parentTarget = Join-Path $repositoryRoot ".e2e-data\parent-target-$([guid]::NewGuid().ToString('N'))"
        $parentLink = Join-Path $repositoryRoot ".e2e-data\parent-link-$([guid]::NewGuid().ToString('N'))"
        [void][IO.Directory]::CreateDirectory($parentTarget)
        [void](New-Item -ItemType Junction -Path $parentLink -Target $parentTarget)
        $junctions.Add($parentLink); $sentinels.Add($parentTarget)
        Assert-Throws { New-E2eManagedRunDirectory $repositoryRoot $parentLink ([guid]::NewGuid()) } 'parent junction rejection'

        $nestedGuid = [guid]::NewGuid()
        $nestedRun = New-E2eManagedRunDirectory $repositoryRoot $runsRoot $nestedGuid
        $createdRuns.Add([pscustomobject]@{ Guid = $nestedGuid; Path = $nestedRun })
        $outsideSentinel = Join-Path $repositoryRoot ".e2e-data\outside-sentinel-$([guid]::NewGuid().ToString('N'))"
        [void][IO.Directory]::CreateDirectory($outsideSentinel)
        [IO.File]::WriteAllText((Join-Path $outsideSentinel 'keep.txt'), 'must survive')
        $sentinels.Add($outsideSentinel)
        $nestedLink = Join-Path $nestedRun 'nested-junction'
        [void](New-Item -ItemType Junction -Path $nestedLink -Target $outsideSentinel)
        $junctions.Add($nestedLink)
        Assert-Throws { Assert-E2eNoReparsePath $repositoryRoot (Join-Path $nestedLink 'keep.txt') } 'nested junction rejection before write'
        Assert-Throws { Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $nestedGuid $nestedRun } 'cleanup refusal on nested reparse point'
        Assert-Equal ([IO.File]::ReadAllText((Join-Path $outsideSentinel 'keep.txt'))) 'must survive' 'outside-run data remains untouched'
        [IO.Directory]::Delete($nestedLink)
        [void]$junctions.Remove($nestedLink)
        Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $nestedGuid $nestedRun
        [void]$createdRuns.Remove(($createdRuns | Where-Object Path -eq $nestedRun | Select-Object -First 1))
    }

    $validJson = @'
{
  "name": "reawote-e2e",
  "services": {
    "database": {
      "volumes": [
        { "type": "volume", "source": "postgres_data", "target": "/var/lib/postgresql", "volume": {} }
      ]
    }
  },
  "volumes": {
    "postgres_data": { "name": "reawote-e2e-postgres-data" }
  }
}
'@ | ConvertFrom-Json
    Assert-Equal (Assert-E2eComposeDatabaseVolume $validJson 'reawote-e2e-postgres-data') 'postgres_data' 'PostgreSQL 18 mount target'
    Assert-Equal (Assert-E2eComposeDatabaseVolume $validJson 'reawote-e2e-postgres-data') 'postgres_data' 'logical Compose source resolves to engine volume name'

    $wrongTargetJson = '{"services":{"database":{"environment":{"POSTGRES_PASSWORD":"must-not-leak"},"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql/data"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"}}}' | ConvertFrom-Json
    Assert-ThrowsWithMessage {
        Assert-E2eComposeDatabaseVolume $wrongTargetJson 'reawote-e2e-postgres-data'
    } @("type='volume'", "source='postgres_data'", "target='/var/lib/postgresql/data'") 'Compose rejects wrong PostgreSQL target with safe mount details' @('must-not-leak')

    $twoMountsJson = '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"},{"type":"volume","source":"other_data","target":"/var/lib/postgresql/18/docker"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"},"other_data":{"name":"unexpected-volume"}}}' | ConvertFrom-Json
    Assert-ThrowsWithMessage {
        Assert-E2eComposeDatabaseVolume $twoMountsJson 'reawote-e2e-postgres-data'
    } @("source='postgres_data'", "target='/var/lib/postgresql'", "source='other_data'", "target='/var/lib/postgresql/18/docker'") 'Compose rejects two database mounts'

    $bindMountJson = '{"services":{"database":{"volumes":[{"type":"bind","source":"C:\\\\unsafe\\\\database","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"}}}' | ConvertFrom-Json
    Assert-ThrowsWithMessage {
        Assert-E2eComposeDatabaseVolume $bindMountJson 'reawote-e2e-postgres-data'
    } @("type='bind'", "target='/var/lib/postgresql'") 'Compose rejects bind mount instead of named volume'

    foreach ($case in @(
        @('wrong engine volume name', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"foreign-volume"}}}'),
        @('anonymous volume', '{"services":{"database":{"volumes":[{"type":"volume","target":"/var/lib/postgresql"}]}},"volumes":{}}'),
        @('missing top-level volume definition', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{}}'),
        @('external volume', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data","external":true}}}'),
        @('tmpfs mount', '{"services":{"database":{"volumes":[{"type":"tmpfs","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"}}}'),
        @('volume driver bind options', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data","driver":"local","driver_opts":{"type":"none","o":"bind","device":"C:\\\\unsafe"}}}}')
    )) {
        $name, $json = $case
        Assert-Throws { Assert-E2eComposeDatabaseVolume ($json | ConvertFrom-Json) 'reawote-e2e-postgres-data' } "Compose rejects $name"
    }

    $validRuntimeMount = @('{"Type":"volume","Name":"reawote-e2e-postgres-data","Source":"/var/lib/docker/volumes/reawote-e2e-postgres-data/_data","Destination":"/var/lib/postgresql"}' | ConvertFrom-Json)
    Assert-Equal (Assert-E2eRuntimeDatabaseVolume $validRuntimeMount 'reawote-e2e-postgres-data') 'reawote-e2e-postgres-data' 'runtime PostgreSQL 18 named-volume mount'
    $extraRuntimeMount = @($validRuntimeMount[0], ('{"Type":"bind","Source":"C:\\\\unexpected","Destination":"/var/lib/postgresql/18/docker"}' | ConvertFrom-Json))
    Assert-ThrowsWithMessage {
        Assert-E2eRuntimeDatabaseVolume $extraRuntimeMount 'reawote-e2e-postgres-data'
    } @("source='reawote-e2e-postgres-data'", "target='/var/lib/postgresql'", "type='bind'", "target='/var/lib/postgresql/18/docker'") 'runtime rejects additional data-directory mount'

    Assert-Equal (Test-E2eLocalDockerEndpoint 'npipe:////./pipe/docker_engine' $true) $true 'local Windows Docker named pipe'
    Assert-Equal (Test-E2eLocalDockerEndpoint 'unix:///var/run/docker.sock' $false) $true 'local Unix Docker socket'
    Assert-Equal (Test-E2eLocalDockerEndpoint 'tcp://127.0.0.1:2375' $true) $false 'Docker tcp endpoint rejection'
    Assert-Equal (Test-E2eLocalDockerEndpoint 'ssh://builder.example.invalid' $false) $false 'Docker ssh endpoint rejection'

    $runnerText = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'test-demo-e2e.ps1'))
    Assert-Equal ($runnerText -notmatch '(?i)docker\s+volume\s+rm') $true 'runner never invokes docker volume rm'
    Assert-Equal ($runnerText -notmatch '(?i)--volumes\b') $true 'runner never removes Compose volumes'

    $mutexName = "Local\ReawoteDemoE2E-helper-$([guid]::NewGuid().ToString('N'))"
    $firstMutex = Enter-E2eRunMutex $mutexName
    try {
        $helperPath = Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1'
        $job = Start-Job -ScriptBlock {
            param($Path, $Name)
            . $Path
            try {
                $candidate = Enter-E2eRunMutex $Name
                Exit-E2eRunMutex $candidate
                return 'ACQUIRED'
            }
            catch { return 'REFUSED' }
        } -ArgumentList $helperPath, $mutexName
        $mutexResult = Receive-Job -Job $job -Wait
        Remove-Job -Job $job -Force
        Assert-Equal ($mutexResult -join '') 'REFUSED' 'second concurrent run mutex'
    }
    finally { Exit-E2eRunMutex $firstMutex }
}
finally {
    foreach ($junction in @($junctions)) { if (Test-Path -LiteralPath $junction) { [IO.Directory]::Delete($junction) } }
    foreach ($run in @($createdRuns)) {
        if (Test-Path -LiteralPath $run.Path) { Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $run.Guid $run.Path }
    }
    foreach ($sentinel in @($sentinels)) {
        if (Test-Path -LiteralPath (Join-Path $sentinel 'keep.txt')) { Remove-Item -LiteralPath (Join-Path $sentinel 'keep.txt') -Force }
        if (Test-Path -LiteralPath $sentinel) { Remove-Item -LiteralPath $sentinel -Force }
    }
}

Write-Host 'All demo E2E helper safety tests passed.'
