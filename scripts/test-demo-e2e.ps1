$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
. (Join-Path $PSScriptRoot 'e2e-runner-operations.ps1')

$script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
$script:E2eCleanupFailed = $false

$projectName = 'reawote-e2e'
$databaseVolumeName = 'reawote-e2e-postgres-data'
$databaseName = 'reawote_e2e'
$databaseUser = 'reawote_e2e'

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Assert-NodeVersion {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 22 or newer is required.' }
    $versionText = (Invoke-E2eReadCommand -Executable 'node' -Arguments @('--version') -OperationId 'node_version_check') -join ''
    $match = [regex]::Match($versionText.Trim(), '^v(?<major>[0-9]+)\.')
    if (-not $match.Success -or [int]$match.Groups['major'].Value -lt 22) {
        throw "Node.js 22 or newer is required; found '$versionText'."
    }
}

function Get-ExactVolumeInspection {
    param([Parameter(Mandatory)] [string] $Name)
    $names = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('volume', 'ls', '--format', '{{.Name}}') -OperationId 'volume_inspection')
    if ($names -notcontains $Name) { return $null }
    $json = (Invoke-E2eReadCommand -Executable 'docker' -Arguments @('volume', 'inspect', $Name) -OperationId 'volume_inspection') -join [Environment]::NewLine
    try {
        $inspection = @($json | ConvertFrom-Json -ErrorAction Stop)
    }
    catch {
        throw 'Docker returned invalid structured JSON for the expected E2E volume.'
    }
    if ($inspection.Count -ne 1 -or $inspection[0].Name -ne $Name) {
        throw "Docker returned an unexpected inspection for volume '$Name'."
    }
    return $inspection[0]
}

function Assert-E2eDatabaseEngineVolume {
    param([switch] $RequirePresent)

    $volume = Get-ExactVolumeInspection -Name $script:E2eDatabaseVolumeName
    if ($null -eq $volume) {
        if ($RequirePresent) {
            throw 'The expected E2E Docker engine volume is missing.'
        }
        return $null
    }
    [void](Assert-E2eEngineVolumeInspection `
        -Inspection $volume `
        -ExpectedName $script:E2eDatabaseVolumeName `
        -ExpectedProjectName $script:E2eProjectName `
        -ExpectedLogicalVolumeName 'postgres_data')
    return $volume
}

function Get-VolumeFingerprint {
    param([Parameter(Mandatory)] [string] $Name)
    $inspection = Get-ExactVolumeInspection -Name $Name
    if ($null -eq $inspection) { return 'absent' }
    return [ordered]@{
        Name = $inspection.Name; Driver = $inspection.Driver; Scope = $inspection.Scope
        CreatedAt = $inspection.CreatedAt; Mountpoint = $inspection.Mountpoint; Labels = $inspection.Labels
    } | ConvertTo-Json -Depth 10 -Compress
}

function Get-ProjectContainerFingerprint {
    param([Parameter(Mandatory)] [string] $Name)
    $lines = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('ps', '--all', '--filter', "label=com.docker.compose.project=$Name", '--format', '{{.ID}}|{{.Names}}|{{.State}}') -OperationId 'protected_state_check')
    return (@($lines | Sort-Object) -join [Environment]::NewLine)
}

function Get-ProtectedState {
    return [ordered]@{
        RegularProject = Get-ProjectContainerFingerprint -Name 'reawote'
        DemoProject = Get-ProjectContainerFingerprint -Name 'reawote-demo'
        RegularVolume = Get-VolumeFingerprint -Name 'reawote_postgres_data'
        DemoVolume = Get-VolumeFingerprint -Name 'reawote-demo-postgres-data'
    } | ConvertTo-Json -Depth 10 -Compress
}

function Assert-E2eResourcesOwned {
    $containerIds = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('ps', '--all', '--quiet', '--filter', "label=com.docker.compose.project=$script:E2eProjectName") -OperationId 'runtime_resources_check')
    foreach ($containerId in $containerIds) {
        if ([string]::IsNullOrWhiteSpace($containerId)) { continue }
        $inspectionJson = (Invoke-E2eReadCommand -Executable 'docker' -Arguments @('inspect', $containerId) -OperationId 'container_inspection') -join [Environment]::NewLine

        $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
        if ($inspection.Count -ne 1) { throw 'Docker returned an unexpected existing E2E container inspection result.' }
        $label = [string](Get-E2eObjectPropertyValue $inspection[0].Config.Labels 'com.docker.compose.project')
        if ($label -ne $script:E2eProjectName) { throw 'Refusing cleanup: container is not owned by the exact E2E project.' }
    }
    $networkIds = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('network', 'ls', '--quiet', '--filter', "label=com.docker.compose.project=$script:E2eProjectName") -OperationId 'runtime_resources_check')
    foreach ($networkId in $networkIds) {
        if ([string]::IsNullOrWhiteSpace($networkId)) { continue }
        $inspectionJson = (Invoke-E2eReadCommand -Executable 'docker' -Arguments @('network', 'inspect', $networkId) -OperationId 'network_inspection') -join [Environment]::NewLine

        $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
        if ($inspection.Count -ne 1) { throw 'Docker returned an unexpected existing E2E network inspection result.' }
        $label = [string](Get-E2eObjectPropertyValue $inspection[0].Labels 'com.docker.compose.project')
        if ($label -ne $script:E2eProjectName) { throw 'Refusing cleanup: network is not owned by the exact E2E project.' }
    }
    [void](Assert-E2eDatabaseEngineVolume)
}

function Assert-NoE2eRuntimeResources {
    $containers = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('ps', '--all', '--quiet', '--filter', "label=com.docker.compose.project=$script:E2eProjectName") -OperationId 'runtime_resources_check')
    $networks = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('network', 'ls', '--quiet', '--filter', "label=com.docker.compose.project=$script:E2eProjectName") -OperationId 'runtime_resources_check')
    if (@($containers | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) { throw 'E2E containers remain after cleanup.' }
    if (@($networks | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) { throw 'E2E networks remain after cleanup.' }
    Assert-E2eResourcesOwned
}

function Test-E2eRuntimeResourcesExist {
    $containers = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('ps', '--all', '--quiet', '--filter', "label=com.docker.compose.project=$script:E2eProjectName") -OperationId 'runtime_resources_check')
    $networks = @(Invoke-E2eReadCommand -Executable 'docker' -Arguments @('network', 'ls', '--quiet', '--filter', "label=com.docker.compose.project=$script:E2eProjectName") -OperationId 'runtime_resources_check')
    return (@($containers | Where-Object { $_ }).Count -gt 0 -or @($networks | Where-Object { $_ }).Count -gt 0)
}

function Invoke-E2eCompose {
    param([string[]] $Arguments, [string] $Step, [switch] $Mutation,
        [string] $OperationId = 'runtime_resources_check', [int] $TimeoutMilliseconds = 60000)
    if ($Mutation) { [void](Assert-LocalDockerContext) }
    $processArguments = @('compose', '--project-name', $script:E2eProjectName, '-f', $script:BaseComposePath, '-f', $script:E2eComposePath) + $Arguments
    if ($Mutation) {
        Set-E2eRunnerOperation -OperationId $OperationId
        $dockerPath = (Get-Command docker -CommandType Application -ErrorAction Stop).Source
        Invoke-E2eRunnerPrivateProcess -FilePath $dockerPath -Arguments $processArguments `
            -OperationId $OperationId -TimeoutMilliseconds $TimeoutMilliseconds
    }
    else {
        Invoke-E2eReadCommand -Executable 'docker' -Arguments $processArguments -OperationId $OperationId
    }
}

function Invoke-E2eComposeWithStandardInput {
    param(
        [Parameter(Mandatory)] [string] $StandardInput,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step,
        [switch] $Mutation,
        [switch] $Bootstrap
    )
    if ($Mutation) { [void](Assert-LocalDockerContext) }
    $operationId = if ($Bootstrap) { 'administrator_bootstrap' } else { 'postgres_schema_reset' }
    Set-E2eRunnerOperation -OperationId $operationId
    $dockerPath = (Get-Command docker -CommandType Application -ErrorAction Stop).Source
    $processArguments = @('compose', '--project-name', $script:E2eProjectName, '-f', $script:BaseComposePath, '-f', $script:E2eComposePath) + $Arguments
    Invoke-E2eRunnerPrivateProcess -FilePath $dockerPath -Arguments $processArguments `
        -StandardInput $StandardInput -OperationId $operationId -Bootstrap:$Bootstrap
}

function Assert-RenderedE2eCompose {
    param([Parameter(Mandatory)] [string] $Json)
    $configuration = $Json | ConvertFrom-Json -ErrorAction Stop
    if ($configuration.name -ne $script:E2eProjectName) { throw 'Rendered Compose project is not the exact E2E project.' }
    if ($Json.Contains('reawote-demo-postgres-data')) { throw 'Rendered E2E Compose unexpectedly references the protected demo volume.' }
    $backendEnvironment = $configuration.services.backend.environment
    if ([string](Get-E2eObjectPropertyValue $backendEnvironment 'APP_ENV') -ne 'e2e' -or
        [string](Get-E2eObjectPropertyValue $backendEnvironment 'CORS_ORIGINS') -ne "http://127.0.0.1:$($script:E2eFrontendPort)" -or
        [string](Get-E2eObjectPropertyValue $backendEnvironment 'AUTH_COOKIE_SECURE') -ne 'false' -or
        [string](Get-E2eObjectPropertyValue $backendEnvironment 'AUTH_ALLOW_INSECURE_COOKIE') -ne 'true') {
        throw 'Rendered E2E backend must use the explicit insecure-cookie exception only for its loopback origin.'
    }
    foreach ($serviceProperty in $configuration.services.PSObject.Properties) {
        $portsProperty = $serviceProperty.Value.PSObject.Properties['ports']
        if ($null -eq $portsProperty) { continue }
        foreach ($publishedPort in @($portsProperty.Value)) {
            if ($null -ne $publishedPort -and $publishedPort.host_ip -ne '127.0.0.1') {
                throw "Service '$($serviceProperty.Name)' publishes outside 127.0.0.1."
            }
        }
    }
    [void](Assert-E2eComposeDatabaseVolume -Configuration $configuration -ExpectedEngineName $script:E2eDatabaseVolumeName)
    $workerMounts = @($configuration.services.worker.volumes | Where-Object { $_.target -eq '/e2e-materials' -and $_.type -eq 'bind' })
    if ($workerMounts.Count -ne 1 -or $workerMounts[0].read_only -ne $true) { throw 'Rendered E2E worker must have one read-only materials bind mount.' }
    $renderedSource = [IO.Path]::GetFullPath($workerMounts[0].source)
    if (-not $renderedSource.Equals($script:E2eMaterialsRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Rendered worker bind source is not the exact repository-local E2E materials root.'
    }
}

function Assert-RuntimeDatabaseMount {
    $containerId = (Invoke-E2eCompose -Arguments @('ps', '--quiet', 'database') -Step 'Locate E2E database container') -join ''
    if ([string]::IsNullOrWhiteSpace($containerId)) { throw 'E2E database container was not found after Compose up.' }
    $inspectionJson = (Invoke-E2eReadCommand -Executable 'docker' -Arguments @('inspect', $containerId.Trim()) -OperationId 'database_mount_validation') -join [Environment]::NewLine
    $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
    if ($inspection.Count -ne 1) {
        throw 'Docker returned an unexpected database container inspection result.'
    }
    $containerLabels = Get-E2eObjectPropertyValue $inspection[0].Config 'Labels'
    $containerProject = [string](Get-E2eObjectPropertyValue $containerLabels 'com.docker.compose.project')
    $containerService = [string](Get-E2eObjectPropertyValue $containerLabels 'com.docker.compose.service')
    if ($containerProject -ne $script:E2eProjectName -or $containerService -ne 'database') {
        throw 'Runtime database container does not have the exact expected Compose project and service labels.'
    }
    [void](Assert-E2eRuntimeDatabaseVolume -Mounts @($inspection[0].Mounts) -ExpectedEngineName $script:E2eDatabaseVolumeName)
}

function Reset-E2eDatabaseSchema {
    param([Parameter(Mandatory)] [string] $Password)

    Assert-E2eResourcesOwned
    Assert-RuntimeDatabaseMount
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume -RequirePresent)
    } -Action {
        $passwordLiteral = $Password.Replace("'", "''")
        $statement = "ALTER ROLE reawote_e2e WITH PASSWORD '$passwordLiteral';`nDROP SCHEMA public CASCADE;`nCREATE SCHEMA public;"
        Invoke-E2eComposeWithStandardInput -StandardInput $statement -Arguments @(
            'exec', '--no-TTY', 'database',
            'psql', '--username', $script:E2eDatabaseUser, '--dbname', $script:E2eDatabaseName,
            '--set', 'ON_ERROR_STOP=1'
        ) -Step 'Reset only the dedicated E2E database schema' -Mutation
    }
}

function Invoke-E2eJsonPost {
    param([string] $BackendUrl, [string] $Route, $Body)
    Set-E2eRunnerOperation -OperationId 'fixture_seed'
    $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$BackendUrl$Route" -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 10 -Compress)
    if ([int]$response.StatusCode -ne 201) { throw "POST $Route returned $($response.StatusCode), expected 201." }
    return $response.Content | ConvertFrom-Json -ErrorAction Stop
}

function New-E2eMaterial {
    param([string] $BackendUrl, [string] $ProjectId, [string] $BrandId, [string] $ProcessorId, [string] $Name)
    return Invoke-E2eJsonPost -BackendUrl $BackendUrl -Route '/api/materials' -Body @{
        project_id = $ProjectId; published_brand_id = $BrandId; material_name = $Name
        main_category_code = 'G03'; assigned_processor_id = $ProcessorId
    }
}

function New-E2eSeedManifestData {
    param([string] $BackendUrl, [string] $RepositoryRoot, [string] $RunRoot, [string] $MaterialsRoot)
    $company = Invoke-E2eJsonPost $BackendUrl '/api/companies' @{ name = 'E2E Company'; legal_name = 'E2E Company (disposable)'; country = 'CZ'; is_active = $true }
    $brand = Invoke-E2eJsonPost $BackendUrl '/api/brands' @{ company_id = $company.id; name = 'E2E Published Brand'; folder_prefix = 'E2E_SAFE'; brand_identifier = 'e2e-disposable-brand'; is_active = $true }
    $project = Invoke-E2eJsonPost $BackendUrl '/api/projects' @{ company_id = $company.id; project_number = 'E2E-001'; name = 'E2E Disposable Project'; status = 'IN_PROGRESS' }
    $processor = Invoke-E2eJsonPost $BackendUrl '/api/internal-users' @{ display_name = 'E2E Processor'; email = 'e2e.processor@example.invalid'; role = 'PROCESSOR'; is_active = $true }
    $valid = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Valid Metadata'
    $missing = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Missing Metadata'
    $mismatch = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Identity Mismatch'
    $validPath = "e2e-library/$($valid.technical_identity)"
    $missingPath = "e2e-library/$($missing.technical_identity)"
    $mismatchPath = 'e2e-library/E2E_WRONG_FOLDER_G03'
    foreach ($relativePath in @($validPath, $missingPath, $mismatchPath)) {
        $directory = Join-Path $MaterialsRoot ($relativePath -replace '/', [IO.Path]::DirectorySeparatorChar)
        [void](New-E2eSafeDirectory -RepositoryRoot $RepositoryRoot -RunRoot $RunRoot -Path (Join-Path $directory '16K'))
    }
    $metadataPath = Join-Path (Join-Path $MaterialsRoot ($validPath -replace '/', [IO.Path]::DirectorySeparatorChar)) 'metadata.txt'
    Write-E2eSafeTextFile -RepositoryRoot $RepositoryRoot -RunRoot $RunRoot -Path $metadataPath -Content (@{
        COLOR = @{ hex = '#A1B2C3' }; TEXTURE_SIZE = @{ cm = @{ width = 12.5; height = 34 } }
    } | ConvertTo-Json -Depth 10 -Compress)
    $fixture = { param($item, $relativePath) [ordered]@{
        id = [string]$item.id; technical_identity = [string]$item.technical_identity; material_name = [string]$item.material_name
        folder_path = $item.folder_path; workflow_status = [string]$item.workflow_status; relativePath = $relativePath
    }}
    return [ordered]@{
        companyId = [string]$company.id; brandId = [string]$brand.id; projectId = [string]$project.id
        valid = & $fixture $valid $validPath; missing = & $fixture $missing $missingPath; mismatch = & $fixture $mismatch $mismatchPath
    }
}

function Save-E2eFailureDiagnostics {
    param([string] $RepositoryRoot, [string] $ArtifactRoot, [string] $RunRoot,
        [string[]] $Secrets, [string] $FailureCode)
    # Serialize only allowlisted state. Never fetch or store a raw failure log.
    $text = ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode $FailureCode
    Write-E2eSafeTextFile -RepositoryRoot $RepositoryRoot -RunRoot $ArtifactRoot `
        -Path (Join-Path $ArtifactRoot 'runner-diagnostics.txt') -Content $text
}

function Assert-E2eRuntimeLogsSafe {
    param([string[]] $Secrets)
    Set-E2eDiagnosticLogCheck -State $script:E2eDiagnostics -Status 'not_run'
    $logText = $null
    try {
        $logText = (@(Invoke-E2eCompose -Arguments @('logs', '--no-color', '--tail', '300', 'database', 'backend', 'worker', 'frontend') `
            -Step 'Check runtime logs' -OperationId 'runtime_log_scan') -join [Environment]::NewLine)
        if (Test-E2eTextContainsSecret -Text $logText -Secrets $Secrets) {
            Set-E2eDiagnosticLogCheck -State $script:E2eDiagnostics -Status 'failed'
            Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'validation_failure' -ExitCode $null
            throw 'E2E_LOG_SCAN_FAILED'
        }
        Set-E2eDiagnosticLogCheck -State $script:E2eDiagnostics -Status 'passed'
    }
    finally { $logText = $null }
}

$script:E2eProjectName = $projectName
$script:E2eDatabaseVolumeName = $databaseVolumeName
$script:E2eDatabaseName = $databaseName
$script:E2eDatabaseUser = $databaseUser
$script:DockerContextVerified = $false
$runGuid = [guid]::NewGuid()
$mutex = $null
$runFailure = $null
$safeFailureCode = 'E2E_PREREQUISITE_FAILED'
$environmentNames = @(
    'BACKEND_PORT', 'FRONTEND_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
    'COMPOSE_PROJECT_NAME', 'AUTH_COOKIE_SECURE', 'AUTH_ALLOW_INSECURE_COOKIE',
    'E2E_FRONTEND_PORT', 'E2E_MATERIALS_ROOT', 'E2E_WORKER_PORT', 'E2E_RUN_MANIFEST',
    'E2E_RUN_TOKEN', 'E2E_AUTH_EMAIL', 'E2E_AUTH_INITIAL_PASSWORD', 'E2E_AUTH_PASSWORD'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    if (@(Get-E2eCredentialEnvironmentNames) -notcontains $name) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
}
$repositoryRoot = $runRoot = $artifactRoot = $dataManagedRoot = $artifactManagedRoot = $null
$postgresPassword = $authEmail = $authInitialPassword = $authPassword = $runToken = $protectedBefore = $null
$startupAttempted = $testPassed = $false

try {
    Start-E2eRunnerPhase -Phase 'safety_preflight' -OperationId 'repository_validation'
    Clear-E2eCredentialEnvironment
    $scriptPath = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $MyInvocation.MyCommand.Path).ProviderPath)
    $repositoryRoot = Assert-LocalE2eRepositoryRoot -RepositoryRoot (Split-Path -Parent (Split-Path -Parent $scriptPath))
    $script:BaseComposePath = Join-Path $repositoryRoot 'docker-compose.yml'
    $script:E2eComposePath = Join-Path $repositoryRoot 'docker-compose.e2e.yml'
    $frontendRoot = Join-Path $repositoryRoot 'frontend'
    $playwrightCli = Join-Path $frontendRoot 'node_modules/playwright/cli.js'
    foreach ($requiredPath in @($script:BaseComposePath, $script:E2eComposePath, $frontendRoot, $playwrightCli)) {
        Assert-E2eNoReparsePath -Root $repositoryRoot -Target $requiredPath
        if (-not (Test-Path -LiteralPath $requiredPath)) { throw 'E2E_PREREQUISITE_FAILED' }
    }
    Assert-NodeVersion
    $nodePath = (Get-Command node -CommandType Application -ErrorAction Stop).Source
    $mutex = Enter-E2eRunMutex
    Complete-E2eRunnerPhase

    $safeFailureCode = 'E2E_ISOLATION_FAILED'
    Start-E2eRunnerPhase -Phase 'docker_context_validation' -OperationId 'docker_context_validation'
    if (-not (Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)) {
        $safeFailureCode = 'E2E_DOCKER_UNAVAILABLE'
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'process_start' -ExitCode $null
        throw 'E2E_DOCKER_UNAVAILABLE'
    }
    [void](Assert-LocalDockerContext)
    $script:DockerContextVerified = $true
    Complete-E2eRunnerPhase

    Start-E2eRunnerPhase -Phase 'compose_config_validation' -OperationId 'compose_config_render'
    $dataManagedRoot = Join-Path $repositoryRoot '.e2e-data\runs'
    $artifactManagedRoot = Join-Path $repositoryRoot '.e2e-artifacts'
    $runRoot = New-E2eManagedRunDirectory $repositoryRoot $dataManagedRoot $runGuid
    $artifactRoot = New-E2eManagedRunDirectory $repositoryRoot $artifactManagedRoot $runGuid
    $script:E2eMaterialsRoot = [IO.Path]::GetFullPath((Join-Path $runRoot 'materials'))
    [void](New-E2eSafeDirectory $repositoryRoot $runRoot $script:E2eMaterialsRoot)
    $allocatedPorts = [Collections.Generic.HashSet[int]]::new()
    while ($allocatedPorts.Count -lt 3) { [void]$allocatedPorts.Add((Get-FreeTcpPort)) }
    $selectedPorts = @($allocatedPorts)
    $backendPort, $frontendPort, $workerPort = $selectedPorts[0], $selectedPorts[1], $selectedPorts[2]
    $script:E2eFrontendPort = $frontendPort
    $backendUrl, $frontendUrl = "http://127.0.0.1:$backendPort", "http://127.0.0.1:$frontendPort"
    $postgresPassword = [guid]::NewGuid().ToString('N')
    $authEmail = "e2e.admin.$($runGuid.ToString('N'))@example.invalid"
    $authInitialPassword = New-E2eSyntheticPassword
    do { $authPassword = New-E2eSyntheticPassword } while ($authPassword -eq $authInitialPassword)
    $env:BACKEND_PORT = "127.0.0.1:$backendPort"; $env:FRONTEND_PORT = "127.0.0.1:$frontendPort"
    $env:POSTGRES_DB = $databaseName; $env:POSTGRES_USER = $databaseUser; $env:POSTGRES_PASSWORD = $postgresPassword
    $env:AUTH_COOKIE_SECURE = 'false'; $env:AUTH_ALLOW_INSECURE_COOKIE = 'true'
    $env:COMPOSE_PROJECT_NAME = $projectName; $env:E2E_FRONTEND_PORT = [string]$frontendPort
    $env:E2E_MATERIALS_ROOT = $script:E2eMaterialsRoot; $env:E2E_WORKER_PORT = [string]$workerPort
    $protectedBefore = Get-ProtectedState
    $rendered = (Invoke-E2eCompose @('config', '--format', 'json') 'Validate isolated Compose' -OperationId 'compose_config_render') -join [Environment]::NewLine
    Set-E2eRunnerOperation -OperationId 'compose_config_validation'
    Assert-RenderedE2eCompose $rendered
    $rendered = $null
    Assert-E2eResourcesOwned
    Invoke-E2eCompose @('down', '--remove-orphans') 'Remove previous isolated runtime' -Mutation -OperationId 'previous_runtime_cleanup'
    Assert-NoE2eRuntimeResources
    Complete-E2eRunnerPhase

    $startupAttempted = $true
    $safeFailureCode = 'E2E_START_FAILED'
    Start-E2eRunnerPhase -Phase 'database_start' -OperationId 'docker_database_start'
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume)
    } -Action {
        Invoke-E2eCompose @('up', '--detach', 'database') 'Start isolated database' -Mutation -OperationId 'docker_database_start'
    }
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'database_readiness' -OperationId 'docker_database_readiness'
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume -RequirePresent)
    } -Action {
        Invoke-E2eCompose @('up', '--detach', '--no-recreate', '--wait', '--wait-timeout', '60', 'database') 'Wait for isolated database' -Mutation -OperationId 'docker_database_readiness' -TimeoutMilliseconds 90000
    }
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'database_runtime_validation' -OperationId 'database_mount_validation'
    Assert-E2eResourcesOwned
    Assert-RuntimeDatabaseMount
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'database_schema_reset' -OperationId 'postgres_schema_reset'
    Reset-E2eDatabaseSchema -Password $postgresPassword
    Complete-E2eRunnerPhase

    Start-E2eRunnerPhase -Phase 'browser_installation' -OperationId 'browser_installation'
    Invoke-E2eRunnerPrivateProcess -FilePath $nodePath -Arguments @($playwrightCli, 'install', 'chromium') `
        -OperationId 'browser_installation' -WorkingDirectory $frontendRoot -TimeoutMilliseconds 600000
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'image_build' -OperationId 'docker_image_build'
    Invoke-E2eCompose @('build', 'backend', 'worker', 'frontend') 'Build isolated images' -Mutation -OperationId 'docker_image_build' -TimeoutMilliseconds 1200000
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'application_start' -OperationId 'docker_application_start'
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume -RequirePresent)
    } -Action {
        Invoke-E2eCompose @('up', '--detach', '--no-build', 'backend', 'worker', 'frontend') 'Start isolated applications' -Mutation -OperationId 'docker_application_start' -TimeoutMilliseconds 180000
    }
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'application_readiness' -OperationId 'docker_application_readiness'
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume -RequirePresent)
    } -Action {
        Invoke-E2eCompose @('up', '--detach', '--no-build', '--no-recreate', '--wait', '--wait-timeout', '120', 'backend', 'worker', 'frontend') 'Wait for isolated applications' -Mutation -OperationId 'docker_application_readiness' -TimeoutMilliseconds 180000
    }
    Assert-E2eResourcesOwned
    Assert-RuntimeDatabaseMount
    Complete-E2eRunnerPhase

    $safeFailureCode = 'E2E_BOOTSTRAP_FAILED'
    Start-E2eRunnerPhase -Phase 'administrator_bootstrap' -OperationId 'administrator_bootstrap'
    # The caller owns framing: precisely two LF-terminated password lines.
    $provisioningInput = $authInitialPassword + "`n" + $authInitialPassword + "`n"
    try {
        Invoke-E2eGuardedAction -Validation {
            [void](Assert-E2eDatabaseEngineVolume -RequirePresent)
            Assert-RuntimeDatabaseMount
        } -Action {
            Invoke-E2eComposeWithStandardInput -StandardInput $provisioningInput -Arguments @(
                'exec', '--no-TTY', 'backend', 'python', '-m', 'app.auth.cli',
                '--email', $authEmail, '--display-name', 'E2E QA'
            ) -Step 'Provision the synthetic E2E administrator through the official CLI' -Mutation -Bootstrap
        }
    }
    finally { $provisioningInput = $null }
    Complete-E2eRunnerPhase

    $safeFailureCode = 'E2E_SEED_FAILED'
    Start-E2eRunnerPhase -Phase 'fixture_seed' -OperationId 'fixture_seed'
    $state = New-E2eSeedManifestData $backendUrl $repositoryRoot $runRoot $script:E2eMaterialsRoot
    $runToken = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $manifestPath = Join-Path $runRoot 'run-manifest.json'
    $manifest = [ordered]@{
        schemaVersion = 1; runGuid = $runGuid.ToString('D'); runnerTokenSha256 = Get-E2eSha256Hex $runToken
        frontendUrl = $frontendUrl; backendUrl = $backendUrl; frontendPort = $frontendPort; backendPort = $backendPort
        materialsRoot = $script:E2eMaterialsRoot; state = $state
    } | ConvertTo-Json -Depth 12
    Write-E2eSafeTextFile $repositoryRoot $runRoot $manifestPath $manifest
    $env:E2E_RUN_MANIFEST = $manifestPath; $env:E2E_RUN_TOKEN = $runToken
    $env:E2E_AUTH_EMAIL = $authEmail
    $env:E2E_AUTH_INITIAL_PASSWORD = $authInitialPassword
    $env:E2E_AUTH_PASSWORD = $authPassword
    Complete-E2eRunnerPhase
    $safeFailureCode = 'E2E_PLAYWRIGHT_FAILED'
    Start-E2eRunnerPhase -Phase 'playwright_start' -OperationId 'playwright_start'
    Assert-E2eNoReparsePath -Root $repositoryRoot -Target $playwrightCli
    if (-not (Test-Path -LiteralPath $playwrightCli -PathType Leaf)) { throw 'E2E_PREREQUISITE_FAILED' }
    try {
        Invoke-E2eWithCredentialCleanup -Action {
            Invoke-E2eRunnerPrivateProcess -FilePath $nodePath -Arguments @($playwrightCli, 'test') `
                -OperationId 'playwright_start' -WorkingDirectory $frontendRoot -TimeoutMilliseconds 600000 -OnStarted {
                    Complete-E2eRunnerPhase
                    Start-E2eRunnerPhase -Phase 'playwright_execution' -OperationId 'playwright_execution'
                }
        }
    }
    finally { Clear-E2eCredentialEnvironment }
    Complete-E2eRunnerPhase
    $safeFailureCode = 'E2E_LOG_SCAN_FAILED'
    Set-E2eRunnerOperation -OperationId 'runtime_log_scan'
    Assert-E2eRuntimeLogsSafe -Secrets @($postgresPassword, $authInitialPassword, $authPassword, $runToken)
    $testPassed = $true
}
catch {
    Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'validation_failure' -ExitCode $null
    $runFailure = $safeFailureCode
}
finally {
    # Failure snapshot remains immutable while each cleanup action has its own
    # current operation. Every independent cleanup still runs after a failure.
    Invoke-E2eCleanupOperation -OperationId 'cleanup_environment' -Action { Clear-E2eCredentialEnvironment }
    if ($script:DockerContextVerified) {
        Invoke-E2eCleanupOperation -OperationId 'cleanup_containers' -Action {
            if ($startupAttempted -or (Test-E2eRuntimeResourcesExist)) {
                Assert-E2eResourcesOwned
                Invoke-E2eCompose @('down', '--remove-orphans') 'Clean isolated containers and network' -Mutation -OperationId 'cleanup_containers'
            }
            Assert-NoE2eRuntimeResources
        }
    }
    if ($null -ne $runRoot) {
        Invoke-E2eCleanupOperation -OperationId 'cleanup_run_directory' -Action {
            Remove-E2eManagedRunDirectory $repositoryRoot $dataManagedRoot $runGuid $runRoot
            if (Test-Path -LiteralPath $runRoot) { throw 'E2E_CLEANUP_FAILED' }
        }
    }
    if ($script:DockerContextVerified -and $null -ne $protectedBefore) {
        Invoke-E2eCleanupOperation -OperationId 'protected_state_check' -Action {
            if ((Get-ProtectedState) -ne $protectedBefore) { throw 'E2E_CLEANUP_FAILED' }
        }
    }
    Invoke-E2eCleanupOperation -OperationId 'cleanup_environment' -Action {
        foreach ($name in $previousEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process') }
        Clear-E2eCredentialEnvironment
    }
    if ($null -ne $mutex) {
        Invoke-E2eCleanupOperation -OperationId 'cleanup_mutex' -Action { Exit-E2eRunMutex $mutex }
    }
    if ($null -eq $runFailure -and $script:E2eCleanupFailed) { $runFailure = 'E2E_CLEANUP_FAILED' }
    if ($null -eq $runFailure -and $testPassed -and $null -ne $artifactRoot) {
        Invoke-E2eCleanupOperation -OperationId 'cleanup_artifact_directory' -Action {
            Remove-E2eManagedRunDirectory $repositoryRoot $artifactManagedRoot $runGuid $artifactRoot
        }
        if ($script:E2eCleanupFailed) { $runFailure = 'E2E_CLEANUP_FAILED' }
    }
    if ($null -ne $runFailure -and $null -ne $artifactRoot) {
        Invoke-E2eCleanupOperation -OperationId 'diagnostics_write' -Action {
            Save-E2eFailureDiagnostics -RepositoryRoot $repositoryRoot -ArtifactRoot $artifactRoot `
                -RunRoot $runRoot -Secrets @() -FailureCode $runFailure
        }
    }
    if (-not $script:E2eCleanupFailed) { Complete-E2eRunnerPhase }
    $postgresPassword = $authEmail = $authInitialPassword = $authPassword = $runToken = $null
}

if ($null -ne $runFailure -or $script:E2eCleanupFailed) {
    if ($null -eq $runFailure) { $runFailure = 'E2E_CLEANUP_FAILED' }
    throw (ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode $runFailure)
}
Write-Host 'E2E passed: all six scenarios completed successfully.'
Write-Host 'Cleanup removed only the isolated E2E containers, network and current run directory; the persistent volume was preserved.'
