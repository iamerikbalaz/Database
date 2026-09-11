$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

$projectName = "reawote-e2e"
$databaseVolumeName = "reawote-e2e-postgres-data"
$ownershipMarker = "reawote-e2e-owned-v1"

if ($projectName -ne "reawote-e2e" -or $databaseVolumeName -ne "reawote-e2e-postgres-data") {
    throw "E2E project and volume names must remain the exact reviewed values."
}

function Assert-LastCommandSucceeded {
    param([Parameter(Mandatory)] [string] $Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $listener.Start()
    try {
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Get-ExactVolumeInspection {
    param([Parameter(Mandatory)] [string] $Name)

    $names = @(& docker volume ls --format "{{.Name}}")
    Assert-LastCommandSucceeded "Docker volume listing"
    if ($names -notcontains $Name) {
        return $null
    }
    $json = (& docker volume inspect $Name) -join [System.Environment]::NewLine
    Assert-LastCommandSucceeded "Inspect Docker volume '$Name'"
    $inspection = @($json | ConvertFrom-Json -ErrorAction Stop)
    if ($inspection.Count -ne 1 -or $inspection[0].Name -ne $Name) {
        throw "Docker returned an unexpected inspection for volume '$Name'."
    }
    return $inspection[0]
}

function Get-VolumeFingerprint {
    param([Parameter(Mandatory)] [string] $Name)

    $inspection = Get-ExactVolumeInspection -Name $Name
    if ($null -eq $inspection) {
        return "absent"
    }
    return [ordered]@{
        Name = $inspection.Name
        Driver = $inspection.Driver
        Scope = $inspection.Scope
        CreatedAt = $inspection.CreatedAt
        Mountpoint = $inspection.Mountpoint
        Labels = $inspection.Labels
    } | ConvertTo-Json -Depth 10 -Compress
}

function Get-ProjectContainerFingerprint {
    param([Parameter(Mandatory)] [string] $Name)

    $lines = @(& docker ps --all `
        --filter "label=com.docker.compose.project=$Name" `
        --format "{{.ID}}|{{.Names}}|{{.State}}")
    Assert-LastCommandSucceeded "Inspect Compose project '$Name' containers"
    return (@($lines | Sort-Object) -join [System.Environment]::NewLine)
}

function Get-ProtectedState {
    return [ordered]@{
        RegularProject = Get-ProjectContainerFingerprint -Name "reawote"
        DemoProject = Get-ProjectContainerFingerprint -Name "reawote-demo"
        RegularVolume = Get-VolumeFingerprint -Name "reawote_postgres_data"
        DemoVolume = Get-VolumeFingerprint -Name "reawote-demo-postgres-data"
    } | ConvertTo-Json -Depth 10 -Compress
}

function Assert-E2eResourcesOwned {
    $containerIds = @(& docker ps --all --quiet `
        --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded "Locate existing E2E containers"
    foreach ($containerId in $containerIds) {
        if ([string]::IsNullOrWhiteSpace($containerId)) {
            continue
        }
        $label = (& docker inspect $containerId `
            --format "{{ index .Config.Labels `"com.docker.compose.project`" }}") -join ""
        Assert-LastCommandSucceeded "Inspect existing E2E container"
        if ($label.Trim() -ne $script:E2eProjectName) {
            throw "Refusing cleanup: container is not owned by the exact E2E project."
        }
    }

    $networkIds = @(& docker network ls --quiet `
        --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded "Locate existing E2E networks"
    foreach ($networkId in $networkIds) {
        if ([string]::IsNullOrWhiteSpace($networkId)) {
            continue
        }
        $label = (& docker network inspect $networkId `
            --format "{{ index .Labels `"com.docker.compose.project`" }}") -join ""
        Assert-LastCommandSucceeded "Inspect existing E2E network"
        if ($label.Trim() -ne $script:E2eProjectName) {
            throw "Refusing cleanup: network is not owned by the exact E2E project."
        }
    }

    $volume = Get-ExactVolumeInspection -Name $script:E2eDatabaseVolumeName
    if ($null -ne $volume) {
        $projectLabel = $volume.Labels.'com.docker.compose.project'
        $volumeLabel = $volume.Labels.'com.docker.compose.volume'
        if ($projectLabel -ne $script:E2eProjectName -or $volumeLabel -ne "postgres_data") {
            throw "Refusing cleanup: E2E volume name exists without the expected Compose ownership labels."
        }
    }
}

function Assert-NoE2eRuntimeResources {
    $containers = @(& docker ps --all --quiet `
        --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded "Verify E2E container cleanup"
    $networks = @(& docker network ls --quiet `
        --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded "Verify E2E network cleanup"
    if (@($containers | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
        throw "E2E containers remain after cleanup."
    }
    if (@($networks | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) {
        throw "E2E networks remain after cleanup."
    }
    if ($null -ne (Get-ExactVolumeInspection -Name $script:E2eDatabaseVolumeName)) {
        throw "E2E database volume remains after cleanup."
    }
}

function Test-E2eRuntimeResourcesExist {
    $containers = @(& docker ps --all --quiet `
        --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded "Locate E2E containers before cleanup"
    $networks = @(& docker network ls --quiet `
        --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded "Locate E2E networks before cleanup"
    return (
        @($containers | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -gt 0 -or
        @($networks | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -gt 0 -or
        $null -ne (Get-ExactVolumeInspection -Name $script:E2eDatabaseVolumeName)
    )
}

function Assert-OwnedE2eDataPath {
    param(
        [Parameter(Mandatory)] [string] $TemporaryRoot,
        [Parameter(Mandatory)] [string] $Path,
        [switch] $RequireMarker
    )

    $temporaryRootPath = [System.IO.Path]::GetFullPath($TemporaryRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $targetPath = [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $expectedPath = [System.IO.Path]::GetFullPath(
        (Join-Path $temporaryRootPath "reawote-e2e-data")
    ).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (-not $targetPath.Equals($expectedPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "E2E data path is not the exact reviewed temporary path."
    }
    if (-not (Test-CanonicalPathWithinRoot -Root $temporaryRootPath -Candidate $targetPath)) {
        throw "E2E data path escaped the system temporary directory."
    }
    Assert-ExistingPathComponentsNoReparse -Root $temporaryRootPath -Target $targetPath
    if ($RequireMarker) {
        $markerPath = Join-Path $targetPath ".reawote-e2e-owned"
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
            throw "Refusing cleanup: E2E data ownership marker is missing."
        }
        $markerValue = [System.IO.File]::ReadAllText($markerPath)
        if ($markerValue -ne $script:E2eOwnershipMarker) {
            throw "Refusing cleanup: E2E data ownership marker is invalid."
        }
    }
    return $targetPath
}

function Remove-OwnedE2eData {
    param(
        [Parameter(Mandatory)] [string] $TemporaryRoot,
        [Parameter(Mandatory)] [string] $Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $safePath = Assert-OwnedE2eDataPath `
        -TemporaryRoot $TemporaryRoot `
        -Path $Path `
        -RequireMarker
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

function Invoke-E2eCompose {
    param(
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step
    )

    & docker compose `
        --project-name $script:E2eProjectName `
        -f $script:BaseComposePath `
        -f $script:E2eComposePath `
        @Arguments
    Assert-LastCommandSucceeded $Step
}

function Assert-RenderedE2eCompose {
    param([Parameter(Mandatory)] [string] $Json)

    $configuration = $Json | ConvertFrom-Json -ErrorAction Stop
    if ($configuration.name -ne $script:E2eProjectName) {
        throw "Rendered Compose project is not the exact E2E project."
    }
    if ($Json.Contains("reawote-demo-postgres-data")) {
        throw "Rendered E2E Compose unexpectedly references the protected demo volume."
    }

    foreach ($serviceProperty in $configuration.services.PSObject.Properties) {
        $portsProperty = $serviceProperty.Value.PSObject.Properties["ports"]
        if ($null -eq $portsProperty) {
            continue
        }
        foreach ($publishedPort in @($portsProperty.Value)) {
            if ($null -ne $publishedPort -and $publishedPort.host_ip -ne "127.0.0.1") {
                throw "Service '$($serviceProperty.Name)' publishes outside 127.0.0.1."
            }
        }
    }

    $databaseMounts = @($configuration.services.database.volumes | Where-Object {
        $_.target -eq "/var/lib/postgresql" -and
        $_.type -eq "volume" -and
        $_.source -eq $script:E2eDatabaseVolumeName
    })
    if ($databaseMounts.Count -ne 1) {
        throw "Rendered E2E database does not use the exact dedicated volume."
    }
    $workerMounts = @($configuration.services.worker.volumes | Where-Object {
        $_.target -eq "/e2e-materials" -and $_.type -eq "bind"
    })
    if ($workerMounts.Count -ne 1 -or $workerMounts[0].read_only -ne $true) {
        throw "Rendered E2E worker must have one read-only materials bind mount."
    }
    $renderedSource = [System.IO.Path]::GetFullPath($workerMounts[0].source)
    $expectedSource = [System.IO.Path]::GetFullPath($script:E2eMaterialsRoot)
    if (-not $renderedSource.Equals($expectedSource, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Rendered worker bind source is not the exact temporary E2E materials root."
    }
}

Assert-DockerAvailable
$script:E2eProjectName = $projectName
$script:E2eDatabaseVolumeName = $databaseVolumeName
$script:E2eOwnershipMarker = $ownershipMarker
$repositoryRoot = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).ProviderPath
)
$script:BaseComposePath = Join-Path $repositoryRoot "docker-compose.yml"
$script:E2eComposePath = Join-Path $repositoryRoot "docker-compose.e2e.yml"
$frontendRoot = Join-Path $repositoryRoot "frontend"
foreach ($requiredPath in @($script:BaseComposePath, $script:E2eComposePath, $frontendRoot)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required E2E path is missing: $requiredPath"
    }
}

$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$e2eDataRoot = Join-Path $temporaryRoot "reawote-e2e-data"
$script:E2eMaterialsRoot = Join-Path $e2eDataRoot "materials"
$allocatedPorts = [System.Collections.Generic.HashSet[int]]::new()
while ($allocatedPorts.Count -lt 3) {
    [void]$allocatedPorts.Add((Get-FreeTcpPort))
}
$selectedPorts = @($allocatedPorts)
$backendPort = $selectedPorts[0]
$frontendPort = $selectedPorts[1]
$workerPort = $selectedPorts[2]
$environmentNames = @(
    "BACKEND_PORT",
    "FRONTEND_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "COMPOSE_PROJECT_NAME",
    "E2E_BACKEND_URL",
    "E2E_FRONTEND_PORT",
    "E2E_FRONTEND_URL",
    "E2E_MATERIALS_ROOT",
    "E2E_OUTPUT_DIR",
    "E2E_WORKER_PORT"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}

$protectedBefore = Get-ProtectedState
$runFailure = $null
$cleanupErrors = [System.Collections.Generic.List[string]]::new()
$startupAttempted = $false
$runtimeCleanupVerified = $false
try {
    $env:BACKEND_PORT = "127.0.0.1:$backendPort"
    $env:FRONTEND_PORT = "127.0.0.1:$frontendPort"
    $env:POSTGRES_DB = "reawote_e2e"
    $env:POSTGRES_USER = "reawote_e2e"
    $env:POSTGRES_PASSWORD = [guid]::NewGuid().ToString("N")
    $env:COMPOSE_PROJECT_NAME = $projectName
    $env:E2E_BACKEND_URL = "http://127.0.0.1:$backendPort"
    $env:E2E_FRONTEND_PORT = [string]$frontendPort
    $env:E2E_FRONTEND_URL = "http://127.0.0.1:$frontendPort"
    $env:E2E_MATERIALS_ROOT = $script:E2eMaterialsRoot
    $env:E2E_OUTPUT_DIR = Join-Path $e2eDataRoot "playwright-results"
    $env:E2E_WORKER_PORT = [string]$workerPort

    $renderedLines = @(Invoke-E2eCompose `
        -Arguments @("config", "--format", "json") `
        -Step "Render E2E Docker Compose configuration")
    Assert-RenderedE2eCompose -Json ($renderedLines -join [System.Environment]::NewLine)

    Assert-E2eResourcesOwned
    Invoke-E2eCompose `
        -Arguments @("down", "--volumes", "--remove-orphans") `
        -Step "Remove verified leftovers from an earlier E2E run"
    Assert-NoE2eRuntimeResources

    if (Test-Path -LiteralPath $e2eDataRoot) {
        Remove-OwnedE2eData -TemporaryRoot $temporaryRoot -Path $e2eDataRoot
    }
    [void][System.IO.Directory]::CreateDirectory($e2eDataRoot)
    [void](Assert-OwnedE2eDataPath -TemporaryRoot $temporaryRoot -Path $e2eDataRoot)
    [System.IO.File]::WriteAllText(
        (Join-Path $e2eDataRoot ".reawote-e2e-owned"),
        $ownershipMarker,
        [System.Text.UTF8Encoding]::new($false)
    )
    [void][System.IO.Directory]::CreateDirectory($script:E2eMaterialsRoot)

    Push-Location -LiteralPath $frontendRoot
    try {
        & npm.cmd exec -- playwright install chromium
        Assert-LastCommandSucceeded "Install or verify Playwright Chromium"
    }
    finally {
        Pop-Location
    }

    $startupAttempted = $true
    Invoke-E2eCompose `
        -Arguments @("up", "--build", "--detach", "--wait") `
        -Step "Start isolated E2E environment"

    Push-Location -LiteralPath $frontendRoot
    try {
        & npm.cmd run test:e2e
        Assert-LastCommandSucceeded "Playwright E2E scenarios"
    }
    finally {
        Pop-Location
    }
}
catch {
    $runFailure = $_
}
finally {
    try {
        if ($startupAttempted -or (Test-E2eRuntimeResourcesExist)) {
            Assert-E2eResourcesOwned
            Invoke-E2eCompose `
                -Arguments @("down", "--volumes", "--remove-orphans") `
                -Step "Clean isolated E2E Docker resources"
        }
        Assert-NoE2eRuntimeResources
        $runtimeCleanupVerified = $true
    }
    catch {
        $cleanupErrors.Add("Docker cleanup: $($_.Exception.Message)")
    }
    try {
        if (-not $runtimeCleanupVerified) {
            throw "Refusing to remove E2E data before Docker resource cleanup is verified."
        }
        Remove-OwnedE2eData -TemporaryRoot $temporaryRoot -Path $e2eDataRoot
        if (Test-Path -LiteralPath $e2eDataRoot) {
            throw "Temporary E2E data remains after cleanup."
        }
    }
    catch {
        $cleanupErrors.Add("Temporary data cleanup: $($_.Exception.Message)")
    }
    foreach ($name in $environmentNames) {
        [System.Environment]::SetEnvironmentVariable(
            $name,
            $previousEnvironment[$name],
            "Process"
        )
    }
    try {
        $protectedAfter = Get-ProtectedState
        if ($protectedAfter -ne $protectedBefore) {
            throw "The regular or demo Compose project/volume state changed during E2E."
        }
    }
    catch {
        $cleanupErrors.Add("Protected resource verification: $($_.Exception.Message)")
    }
}

if ($null -ne $runFailure) {
    if ($cleanupErrors.Count -gt 0) {
        throw "E2E failed: $($runFailure.Exception.Message) Cleanup also failed: $($cleanupErrors -join ' | ')"
    }
    throw $runFailure
}
if ($cleanupErrors.Count -gt 0) {
    throw "E2E scenarios passed, but cleanup verification failed: $($cleanupErrors -join ' | ')"
}

Write-Host "All Playwright demo E2E scenarios passed."
Write-Host "Cleanup removed only project '$projectName', volume '$databaseVolumeName' and '$e2eDataRoot'."
Write-Host "Regular project 'reawote' and demo project/volume state remained unchanged."
