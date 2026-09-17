$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

$projectName = if ($env:E2E_PROJECT_NAME) { $env:E2E_PROJECT_NAME } else { 'reawote-e2e' }
if ($projectName -cnotmatch '^reawote-e2e(?:-[a-z0-9][a-z0-9-]{0,40})?$') {
    throw 'E2E_PROJECT_NAME must be reawote-e2e or reawote-e2e- followed by a lowercase local test identifier.'
}
$databaseVolumeName = "$projectName-postgres-data"
$databaseName = 'reawote_e2e'
$databaseUser = 'reawote_e2e'
if ($env:E2E_KEEP_SUCCESS_ARTIFACTS -and $env:E2E_KEEP_SUCCESS_ARTIFACTS -cnotin @('0', '1')) {
    throw 'E2E_KEEP_SUCCESS_ARTIFACTS must be 0 or 1.'
}
$keepSuccessfulArtifacts = $env:E2E_KEEP_SUCCESS_ARTIFACTS -ceq '1'

function Assert-LastCommandSucceeded {
    param([Parameter(Mandatory)] [string] $Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Assert-NodeVersion {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 22 or newer is required.' }
    $versionText = (& node --version) -join ''
    Assert-LastCommandSucceeded 'Read Node.js version'
    $match = [regex]::Match($versionText.Trim(), '^v(?<major>[0-9]+)\.')
    if (-not $match.Success -or [int]$match.Groups['major'].Value -lt 22) {
        throw "Node.js 22 or newer is required; found '$versionText'."
    }
}

function Get-ExactVolumeInspection {
    param([Parameter(Mandatory)] [string] $Name)
    $names = @(& docker volume ls --format '{{.Name}}')
    Assert-LastCommandSucceeded 'Docker volume listing'
    if ($names -notcontains $Name) { return $null }
    $json = (& docker volume inspect $Name) -join [Environment]::NewLine
    Assert-LastCommandSucceeded "Inspect Docker volume '$Name'"
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
    $lines = @(& docker ps --all --filter "label=com.docker.compose.project=$Name" --format '{{.ID}}|{{.Names}}|{{.State}}')
    Assert-LastCommandSucceeded "Inspect Compose project '$Name' containers"
    return (@($lines | Sort-Object) -join [Environment]::NewLine)
}

function Get-ProtectedState {
    return [ordered]@{
        RegularProject = Get-ProjectContainerFingerprint -Name 'reawote'
        DemoProject = Get-ProjectContainerFingerprint -Name 'reawote-demo'
        RegularVolume = Get-VolumeFingerprint -Name 'reawote_postgres_data'
        DemoVolume = Get-VolumeFingerprint -Name 'reawote-demo-postgres-data'
        DefaultE2eVolume = if ($script:E2eProjectName -ne 'reawote-e2e') { Get-VolumeFingerprint -Name 'reawote-e2e-postgres-data' } else { 'owned-by-this-run' }
    } | ConvertTo-Json -Depth 10 -Compress
}

function Assert-E2eResourcesOwned {
    $containerIds = @(& docker ps --all --quiet --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded 'Locate existing E2E containers'
    foreach ($containerId in $containerIds) {
        if ([string]::IsNullOrWhiteSpace($containerId)) { continue }
        $inspectionJson = (& docker inspect $containerId) -join [Environment]::NewLine
        Assert-LastCommandSucceeded 'Inspect existing E2E container'
        $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
        if ($inspection.Count -ne 1) { throw 'Docker returned an unexpected existing E2E container inspection result.' }
        $label = [string](Get-E2eObjectPropertyValue $inspection[0].Config.Labels 'com.docker.compose.project')
        if ($label -ne $script:E2eProjectName) { throw 'Refusing cleanup: container is not owned by the exact E2E project.' }
    }
    $networkIds = @(& docker network ls --quiet --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded 'Locate existing E2E networks'
    foreach ($networkId in $networkIds) {
        if ([string]::IsNullOrWhiteSpace($networkId)) { continue }
        $inspectionJson = (& docker network inspect $networkId) -join [Environment]::NewLine
        Assert-LastCommandSucceeded 'Inspect existing E2E network'
        $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
        if ($inspection.Count -ne 1) { throw 'Docker returned an unexpected existing E2E network inspection result.' }
        $label = [string](Get-E2eObjectPropertyValue $inspection[0].Labels 'com.docker.compose.project')
        if ($label -ne $script:E2eProjectName) { throw 'Refusing cleanup: network is not owned by the exact E2E project.' }
    }
    [void](Assert-E2eDatabaseEngineVolume)
}

function Assert-NoE2eRuntimeResources {
    $containers = @(& docker ps --all --quiet --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded 'Verify E2E container cleanup'
    $networks = @(& docker network ls --quiet --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded 'Verify E2E network cleanup'
    if (@($containers | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) { throw 'E2E containers remain after cleanup.' }
    if (@($networks | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -ne 0) { throw 'E2E networks remain after cleanup.' }
    Assert-E2eResourcesOwned
}

function Test-E2eRuntimeResourcesExist {
    $containers = @(& docker ps --all --quiet --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded 'Locate E2E containers before cleanup'
    $networks = @(& docker network ls --quiet --filter "label=com.docker.compose.project=$script:E2eProjectName")
    Assert-LastCommandSucceeded 'Locate E2E networks before cleanup'
    return (@($containers | Where-Object { $_ }).Count -gt 0 -or @($networks | Where-Object { $_ }).Count -gt 0)
}

function Invoke-E2eCompose {
    param([string[]] $Arguments, [string] $Step, [switch] $Mutation)
    if ($Mutation) { [void](Assert-LocalDockerContext) }
    & docker compose --project-name $script:E2eProjectName -f $script:BaseComposePath -f $script:E2eComposePath @Arguments
    Assert-LastCommandSucceeded $Step
}

function Invoke-E2eComposeWithStandardInput {
    param(
        [Parameter(Mandatory)] [string] $StandardInput,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step,
        [switch] $Mutation
    )
    if ($Mutation) { [void](Assert-LocalDockerContext) }
    $StandardInput | & docker compose --project-name $script:E2eProjectName -f $script:BaseComposePath -f $script:E2eComposePath @Arguments
    Assert-LastCommandSucceeded $Step
}

function Assert-RenderedE2eCompose {
    param([Parameter(Mandatory)] [string] $Json)
    $configuration = $Json | ConvertFrom-Json -ErrorAction Stop
    if ($configuration.name -ne $script:E2eProjectName) { throw 'Rendered Compose project is not the exact E2E project.' }
    if ($Json.Contains('reawote-demo-postgres-data')) { throw 'Rendered E2E Compose unexpectedly references the protected demo volume.' }
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
    foreach ($suffix in @('sources', 'journals')) {
        $logical = 'identity_' + $suffix
        $expected = $env:E2E_IDENTITY_VOLUME_PREFIX + '-' + $suffix
        if ($configuration.volumes.$logical.name -cne $expected) { throw 'Unexpected identity engine volume.' }
        $definition = $configuration.volumes.$logical
        $external = Get-E2eObjectPropertyValue $definition 'external'
        $driver = Get-E2eObjectPropertyValue $definition 'driver'
        $options = Get-E2eObjectPropertyValue $definition 'driver_opts'
        if ($external -eq $true -or ($driver -and $driver -ne 'local') -or ($null -ne $options -and @($options.PSObject.Properties).Count -gt 0)) { throw 'Identity fixtures require fresh local volumes without external drivers or mount options.' }
        $target = if ($suffix -eq 'sources') { '/e2e-materials/e2e-identity' } else { '/e2e-identity-journal' }
        $mount = @($configuration.services.worker.volumes | Where-Object { $_.target -eq $target })
        if ($mount.Count -ne 1 -or $mount[0].type -ne 'volume' -or $mount[0].source -ne $logical) { throw 'Unexpected identity worker volume mount.' }
    }
}

function Assert-RuntimeIdentityMounts {
    $containerId = (Invoke-E2eCompose @('ps', '--quiet', 'worker') 'Locate E2E worker') -join ''
    $inspectionJson = (& docker inspect $containerId.Trim()) -join [Environment]::NewLine
    Assert-LastCommandSucceeded 'Inspect E2E worker'
    $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
    if ($inspection.Count -ne 1 -or $inspection[0].Config.Labels.'com.docker.compose.project' -cne $script:E2eProjectName -or $inspection[0].Config.Labels.'com.docker.compose.service' -ne 'worker') { throw 'Unexpected worker container ownership.' }
    foreach ($suffix in @('sources', 'journals')) {
        $expected = $env:E2E_IDENTITY_VOLUME_PREFIX + '-' + $suffix
        $volume = Get-ExactVolumeInspection $expected
        [void](Assert-E2eEngineVolumeInspection $volume $expected $script:E2eProjectName ('identity_' + $suffix))
        $target = if ($suffix -eq 'sources') { '/e2e-materials/e2e-identity' } else { '/e2e-identity-journal' }
        $mount = @($inspection[0].Mounts | Where-Object { $_.Destination -eq $target })
        if ($mount.Count -ne 1 -or $mount[0].Type -ne 'volume' -or $mount[0].Name -cne $expected -or $mount[0].RW -ne $true) { throw 'Runtime identity mount does not belong to this run.' }
    }
}

function Assert-RuntimeDatabaseMount {
    $containerId = (Invoke-E2eCompose -Arguments @('ps', '--quiet', 'database') -Step 'Locate E2E database container') -join ''
    if ([string]::IsNullOrWhiteSpace($containerId)) { throw 'E2E database container was not found after Compose up.' }
    $inspectionJson = (& docker inspect $containerId.Trim()) -join [Environment]::NewLine
    Assert-LastCommandSucceeded 'Inspect E2E database container mounts'
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

function Invoke-E2eAuthenticatedPost {
    param([string] $BackendUrl, [string] $Route, $Body, [int] $ExpectedStatus = 200)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$BackendUrl$Route" `
            -WebSession $script:E2eWebSession -Headers @{ Origin = $script:E2eFrontendUrl; 'X-CSRF-Token' = $script:E2eCsrf } `
            -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 10 -Compress)
    }
    catch { throw "Authenticated E2E request failed for $Route; response omitted to protect credentials." }
    if ([int]$response.StatusCode -ne $ExpectedStatus) { throw "Unexpected status for $Route." }
    return $response.Content | ConvertFrom-Json -ErrorAction Stop
}

function Invoke-E2eJsonPost {
    param([string] $BackendUrl, [string] $Route, $Body)
    return Invoke-E2eAuthenticatedPost $BackendUrl $Route $Body 201
}

function Initialize-E2eAuthentication {
    param([string] $BackendUrl, [string] $FrontendUrl)
    $script:E2eFrontendUrl = $FrontendUrl
    $initial = [guid]::NewGuid().ToString('N') + '-Initial!'
    $env:E2E_ADMIN_PASSWORD = [guid]::NewGuid().ToString('N') + '-Z!7'
    $env:E2E_TEMPORARY_PASSWORD = [guid]::NewGuid().ToString('N') + '-Temporary!'
    $env:E2E_USER_PASSWORD = [guid]::NewGuid().ToString('N') + '-Personal!'
    $script:E2eWebSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $script:E2eCsrf = ''
    $inputJson = @{ email = 'e2e.operator@example.invalid'; display_name = 'E2E Operator'; password = $initial } | ConvertTo-Json -Compress
    $code = 'import json,sys; from app.auth.cli import provision_first_admin; from app.core.config import get_settings; from app.db.session import Database; s=get_settings(); d=Database(s.resolved_database_url); provision_first_admin(d,s,**json.load(sys.stdin)); d.dispose()'
    Invoke-E2eComposeWithStandardInput -StandardInput $inputJson -Arguments @(
        'exec', '--no-TTY', 'backend', 'python', '-c', $code
    ) -Step 'Provision the isolated E2E administrator' -Mutation
    $login = Invoke-E2eAuthenticatedPost $BackendUrl '/api/auth/login' @{ email = 'e2e.operator@example.invalid'; password = $initial }
    $script:E2eCsrf = $login.csrf_token
    [void](Invoke-E2eAuthenticatedPost $BackendUrl '/api/auth/change-password' @{ current_password = $initial; new_password = $env:E2E_ADMIN_PASSWORD })
    $login = Invoke-E2eAuthenticatedPost $BackendUrl '/api/auth/login' @{ email = 'e2e.operator@example.invalid'; password = $env:E2E_ADMIN_PASSWORD }
    $script:E2eCsrf = $login.csrf_token
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
    [void](Invoke-E2eAuthenticatedPost $BackendUrl "/api/auth/accounts/$($processor.id)/access" @{ current_password = $env:E2E_ADMIN_PASSWORD; new_password = $env:E2E_TEMPORARY_PASSWORD })
    $valid = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Valid Metadata'
    $missing = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Missing Metadata'
    $dimensions = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Unsupported Dimensions'
    $mismatch = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Identity Mismatch'
    $review = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Source Review'
    $approval = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Technical Approval'
    $identity = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Controlled Identity'
    $content = New-E2eMaterial $BackendUrl $project.id $brand.id $processor.id 'E2E Publication Content'
    $identityBrand = Invoke-E2eJsonPost $BackendUrl '/api/brands' @{ company_id = $company.id; name = 'E2E Identity Target'; folder_prefix = 'E2E_NEXT'; brand_identifier = 'e2e-identity-target'; is_active = $true }
    Assert-RuntimeIdentityMounts
    $identityCode = @'
import json, os, sys
from pathlib import Path
from PIL import Image
value = json.load(sys.stdin)
name = value['identity']
assert name.startswith('E2E_SAFE_') and '/' not in name and '\\' not in name
root = Path('/e2e-materials/e2e-identity')
assert root.is_dir() and not root.is_symlink()
source = root / name
source.mkdir()
(source / '1K').mkdir()
(source / 'SOURCE').mkdir()
for shortcut in ('COL', 'NRM', 'ROUGH'):
    Image.new('RGB', (1024,1024), '#607080').save(source / '1K' / f'{name}_{shortcut}_1K.png')
(source / 'SOURCE' / f'{name}.sbs').write_bytes(b'Synthetic E2E source only')
(source / 'metadata.txt').write_text(json.dumps({'FOLDER': name, 'PRODUCT_NAME': value['name'], 'MANUFACTURER': 'E2E Published Brand', 'CATEGORY': 'G03', 'PRODUCT_NUMBER': name.rsplit('_',2)[1], 'COLOR': {'hex':'#607080'}, 'TEXTURE_SIZE': {'cm': {'width':12.5,'height':34}}, 'SOURCE': {'SBS': f'SOURCE/{name}.sbs'}}), encoding='utf-8')
Path('/e2e-identity-journal/private').mkdir(mode=0o700)
'@
    Invoke-E2eComposeWithStandardInput -StandardInput (@{ identity = $identity.technical_identity; name = $identity.material_name } | ConvertTo-Json -Compress) -Arguments @('exec', '--no-TTY', 'worker', 'python', '-c', $identityCode) -Step 'Create synthetic identity fixtures only in verified fresh Linux volumes' -Mutation
    $validPath = "e2e-library/$($valid.technical_identity)"
    $missingPath = "e2e-library/$($missing.technical_identity)"
    $dimensionsPath = "e2e-library/$($dimensions.technical_identity)"
    $mismatchPath = 'e2e-library/E2E_WRONG_FOLDER_G03'
    $reviewPath = "e2e-library/$($review.technical_identity)"
    $approvalPath = "e2e-library/$($approval.technical_identity)"
    foreach ($relativePath in @($validPath, $missingPath, $mismatchPath, $dimensionsPath, $reviewPath)) {
        $directory = Join-Path $MaterialsRoot ($relativePath -replace '/', [IO.Path]::DirectorySeparatorChar)
        [void](New-E2eSafeDirectory -RepositoryRoot $RepositoryRoot -RunRoot $RunRoot -Path (Join-Path $directory '16K'))
    }
    $approvalDirectory = Join-Path $MaterialsRoot ($approvalPath -replace '/', [IO.Path]::DirectorySeparatorChar)
    [void](New-E2eSafeDirectory $RepositoryRoot $RunRoot (Join-Path $approvalDirectory '1K'))
    $generator = Join-Path $PSScriptRoot 'generate-e2e-png.mjs'
    Assert-E2eNoReparsePath -Root $RepositoryRoot -Target $generator
    $encoded = (& node $generator) -join ''
    Assert-LastCommandSucceeded 'Generate a synthetic E2E PNG'
    $png = [Convert]::FromBase64String($encoded)
    foreach ($map in @('COL', 'NRM', 'ROUGH')) {
        Write-E2eSafeBytes $RepositoryRoot $RunRoot (Join-Path (Join-Path $approvalDirectory '1K') "$($approval.technical_identity)_${map}_1K.png") $png
    }
    foreach ($variant in @('front', 'side')) {
        $previewEncoded = (& node $generator "preview-$variant") -join ''
        Assert-LastCommandSucceeded 'Generate synthetic gallery PNG'
        $previewBytes = [Convert]::FromBase64String($previewEncoded)
        foreach ($relativePath in @($validPath, $approvalPath)) {
            $previewDirectory = Join-Path (Join-Path $MaterialsRoot ($relativePath -replace '/', [IO.Path]::DirectorySeparatorChar)) 'PREVIEW'
            [void](New-E2eSafeDirectory $RepositoryRoot $RunRoot $previewDirectory)
            Write-E2eSafeBytes $RepositoryRoot $RunRoot (Join-Path $previewDirectory "$variant.png") $previewBytes
        }
    }
    $metadataPath = Join-Path (Join-Path $MaterialsRoot ($validPath -replace '/', [IO.Path]::DirectorySeparatorChar)) 'metadata.txt'
    Write-E2eSafeTextFile -RepositoryRoot $RepositoryRoot -RunRoot $RunRoot -Path $metadataPath -Content (@{
        COLOR = @{ hex = '#A1B2C3' }; TEXTURE_SIZE = @{ cm = @{ width = 12.5; height = 34 } }
    } | ConvertTo-Json -Depth 10 -Compress)
    $dimensionsFile = Join-Path (Join-Path $MaterialsRoot ($dimensionsPath -replace '/', [IO.Path]::DirectorySeparatorChar)) 'metadata.txt'
    Write-E2eSafeTextFile $RepositoryRoot $RunRoot $dimensionsFile 'texture size: 1.23456x2 cm'
    $fixture = { param($item, $relativePath) [ordered]@{
        id = [string]$item.id; technical_identity = [string]$item.technical_identity; material_name = [string]$item.material_name
        folder_path = $item.folder_path; workflow_status = [string]$item.workflow_status; relativePath = $relativePath
    }}
    return [ordered]@{
        companyId = [string]$company.id; brandId = [string]$brand.id; projectId = [string]$project.id
        identity = & $fixture $identity "e2e-identity/$($identity.technical_identity)"; identityBrandId = [string]$identityBrand.id
        content = & $fixture $content "e2e-library/$($content.technical_identity)"
        valid = & $fixture $valid $validPath; missing = & $fixture $missing $missingPath; mismatch = & $fixture $mismatch $mismatchPath; dimensions = & $fixture $dimensions $dimensionsPath; review = & $fixture $review $reviewPath; approval = & $fixture $approval $approvalPath
    }
}

function Save-E2eFailureDiagnostics {
    param([string] $RepositoryRoot, [string] $ArtifactRoot, [string] $RunRoot, [string] $Password, [string] $FailureMessage)
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add("Runner failure: $FailureMessage")
    if ($script:DockerContextVerified) {
        try {
            $lines.Add(''); $lines.Add('Compose status:')
            foreach ($line in @(Invoke-E2eCompose @('ps', '--all') 'Capture E2E Compose status')) { $lines.Add([string]$line) }
            $lines.Add(''); $lines.Add('Synthetic service logs:')
            foreach ($line in @(& docker compose --project-name $script:E2eProjectName -f $script:BaseComposePath -f $script:E2eComposePath logs --no-color --tail 300 backend worker frontend)) {
                if ($line -match '(?i)raw_content|source_content|authorization|cookie|password|csrf|token') { $lines.Add('[redacted potentially sensitive log line]') }
                else { $lines.Add([string]$line) }
            }
        }
        catch { $lines.Add("Diagnostics collection error: $($_.Exception.Message)") }
    }
    $text = $lines -join [Environment]::NewLine
    foreach ($secret in @($Password, $RunRoot, $env:E2E_RUN_TOKEN, $env:E2E_ADMIN_PASSWORD, $env:E2E_TEMPORARY_PASSWORD, $env:E2E_USER_PASSWORD, $env:E2E_WORKER_MUTATION_TOKEN, $script:E2eCsrf)) { if ($secret) { $text = $text.Replace($secret, '[REDACTED]') } }
    Write-E2eSafeTextFile -RepositoryRoot $RepositoryRoot -RunRoot $ArtifactRoot -Path (Join-Path $ArtifactRoot 'runner-diagnostics.txt') -Content $text
}

$script:E2eProjectName = $projectName
$script:E2eDatabaseVolumeName = $databaseVolumeName
$script:E2eDatabaseName = $databaseName
$script:E2eDatabaseUser = $databaseUser
$script:DockerContextVerified = $false
$script:E2eCsrf = ''
$runGuid = [guid]::NewGuid()
$mutex = $null
$runFailure = $null
$cleanupErrors = [Collections.Generic.List[string]]::new()
$environmentNames = @('BACKEND_PORT', 'FRONTEND_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'COMPOSE_PROJECT_NAME', 'E2E_FRONTEND_PORT', 'E2E_MATERIALS_ROOT', 'E2E_WORKER_PORT', 'E2E_RUN_MANIFEST', 'E2E_RUN_TOKEN', 'E2E_ADMIN_PASSWORD', 'E2E_TEMPORARY_PASSWORD', 'E2E_USER_PASSWORD', 'E2E_RETAINED_PASS', 'E2E_IDENTITY_VOLUME_PREFIX', 'E2E_WORKER_MUTATION_TOKEN')
$previousEnvironment = @{}
foreach ($name in $environmentNames) { $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$repositoryRoot = $runRoot = $artifactRoot = $dataManagedRoot = $artifactManagedRoot = $postgresPassword = $protectedBefore = $null
$startupAttempted = $testPassed = $false

try {
    $scriptPath = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $MyInvocation.MyCommand.Path).ProviderPath)
    $repositoryRoot = Assert-LocalE2eRepositoryRoot -RepositoryRoot (Split-Path -Parent (Split-Path -Parent $scriptPath))
    $script:BaseComposePath = Join-Path $repositoryRoot 'docker-compose.yml'
    $script:E2eComposePath = Join-Path $repositoryRoot 'docker-compose.e2e.yml'
    $frontendRoot = Join-Path $repositoryRoot 'frontend'
    foreach ($requiredPath in @($script:BaseComposePath, $script:E2eComposePath, $frontendRoot)) {
        Assert-E2eNoReparsePath -Root $repositoryRoot -Target $requiredPath
        if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Required E2E path is missing: $requiredPath" }
    }
    Assert-NodeVersion
    $mutex = Enter-E2eRunMutex
    [void](Assert-LocalDockerContext)
    $script:DockerContextVerified = $true
    $dataManagedRoot = Join-Path $repositoryRoot '.e2e-data\runs'
    $artifactManagedRoot = Join-Path $repositoryRoot '.e2e-artifacts'
    $runRoot = New-E2eManagedRunDirectory $repositoryRoot $dataManagedRoot $runGuid
    $artifactRoot = New-E2eManagedRunDirectory $repositoryRoot $artifactManagedRoot $runGuid
    $script:E2eMaterialsRoot = [IO.Path]::GetFullPath((Join-Path $runRoot 'materials'))
    [void](New-E2eSafeDirectory $repositoryRoot $runRoot $script:E2eMaterialsRoot)
    [void](New-E2eSafeDirectory $repositoryRoot $runRoot (Join-Path $script:E2eMaterialsRoot 'e2e-identity'))
    $env:E2E_IDENTITY_VOLUME_PREFIX = $projectName + '-identity-' + $runGuid.ToString('N')
    $env:E2E_WORKER_MUTATION_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    foreach ($suffix in @('sources', 'journals')) {
        if ($null -ne (Get-ExactVolumeInspection ($env:E2E_IDENTITY_VOLUME_PREFIX + '-' + $suffix))) { throw 'Fresh identity volume name already exists; refusing all mutations.' }
    }
    $allocatedPorts = [Collections.Generic.HashSet[int]]::new()
    while ($allocatedPorts.Count -lt 3) { [void]$allocatedPorts.Add((Get-FreeTcpPort)) }
    $selectedPorts = @($allocatedPorts)
    $backendPort, $frontendPort, $workerPort = $selectedPorts[0], $selectedPorts[1], $selectedPorts[2]
    $backendUrl, $frontendUrl = "http://127.0.0.1:$backendPort", "http://127.0.0.1:$frontendPort"
    $postgresPassword = [guid]::NewGuid().ToString('N')
    $env:BACKEND_PORT = [string]$backendPort; $env:FRONTEND_PORT = [string]$frontendPort
    $env:POSTGRES_DB = $databaseName; $env:POSTGRES_USER = $databaseUser; $env:POSTGRES_PASSWORD = $postgresPassword
    $env:COMPOSE_PROJECT_NAME = $projectName; $env:E2E_FRONTEND_PORT = [string]$frontendPort
    $env:E2E_MATERIALS_ROOT = $script:E2eMaterialsRoot; $env:E2E_WORKER_PORT = [string]$workerPort
    $protectedBefore = Get-ProtectedState
    $rendered = (Invoke-E2eCompose @('config', '--format', 'json') 'Render E2E Docker Compose configuration') -join [Environment]::NewLine
    Assert-RenderedE2eCompose $rendered
    Assert-E2eResourcesOwned
    Invoke-E2eCompose @('down', '--remove-orphans') 'Remove verified containers and network from an earlier E2E run' -Mutation
    Assert-NoE2eRuntimeResources
    $startupAttempted = $true
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume)
    } -Action {
        Invoke-E2eCompose @('up', '--detach', '--wait', 'database') 'Start isolated E2E database for verified schema reset' -Mutation
    }
    Assert-E2eResourcesOwned
    Assert-RuntimeDatabaseMount
    Reset-E2eDatabaseSchema -Password $postgresPassword
    Push-Location $frontendRoot
    try { & npm.cmd exec -- playwright install chromium; Assert-LastCommandSucceeded 'Install or verify Playwright Chromium' }
    finally { Pop-Location }
    Invoke-E2eGuardedAction -Validation {
        [void](Assert-E2eDatabaseEngineVolume -RequirePresent)
    } -Action {
        Invoke-E2eCompose @('up', '--build', '--detach', '--wait') 'Start isolated E2E environment' -Mutation
    }
    Assert-E2eResourcesOwned
    Assert-RuntimeDatabaseMount
    Initialize-E2eAuthentication $backendUrl $frontendUrl
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
    Push-Location $frontendRoot
    try {
        Write-Host 'E2E first pass: fresh isolated data.'
        $env:E2E_RETAINED_PASS = '0'
        & npm.cmd exec -- playwright test
        Assert-LastCommandSucceeded 'Playwright E2E scenarios'
        # Restart the application without resetting the database or fixtures.
        Invoke-E2eCompose @('restart', 'backend', 'frontend', 'worker') 'Restart application and worker with retained data and journals' -Mutation
        Assert-RuntimeIdentityMounts
        $deadline = [DateTime]::UtcNow.AddSeconds(45)
        $ready = $false
        while ([DateTime]::UtcNow -lt $deadline) {
            try {
                $health = Invoke-WebRequest -UseBasicParsing -Uri "$backendUrl/health" -TimeoutSec 2
                $web = Invoke-WebRequest -UseBasicParsing -Uri $frontendUrl -TimeoutSec 2
                $workerHealth = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$workerPort/health" -TimeoutSec 2
                if ($health.StatusCode -eq 200 -and $web.StatusCode -eq 200 -and $workerHealth.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'Application did not recover after retained-data restart.' }
        Write-Host 'E2E second pass: same database, files and accounts after application restart.'
        $env:E2E_RETAINED_PASS = '1'
        & npm.cmd exec -- playwright test
        Assert-LastCommandSucceeded 'Playwright E2E retained-data scenarios'
    }
    finally { Pop-Location }
    $testPassed = $true
}
catch { $runFailure = $_ }
finally {
    if ($null -ne $runFailure -and $null -ne $artifactRoot) {
        try { Save-E2eFailureDiagnostics $repositoryRoot $artifactRoot $runRoot $postgresPassword $runFailure.Exception.Message }
        catch { $cleanupErrors.Add("Diagnostics: $($_.Exception.Message)") }
    }
    if ($script:DockerContextVerified) {
        try {
            if ($startupAttempted -or (Test-E2eRuntimeResourcesExist)) {
                Assert-E2eResourcesOwned
                Invoke-E2eCompose @('down', '--remove-orphans') 'Clean isolated E2E containers and network' -Mutation
            }
            Assert-NoE2eRuntimeResources
        }
        catch { $cleanupErrors.Add("Docker cleanup: $($_.Exception.Message)") }
    }
    if ($null -ne $runRoot) {
        try {
            Remove-E2eManagedRunDirectory $repositoryRoot $dataManagedRoot $runGuid $runRoot
            if (Test-Path -LiteralPath $runRoot) { throw "E2E run data remains: $runRoot" }
        }
        catch { $cleanupErrors.Add("Run data cleanup (manual review path '$runRoot'): $($_.Exception.Message)") }
    }
    if ($script:DockerContextVerified -and $null -ne $protectedBefore) {
        try { if ((Get-ProtectedState) -ne $protectedBefore) { throw 'The regular or demo Compose project/volume state changed during E2E.' } }
        catch { $cleanupErrors.Add("Protected resource verification: $($_.Exception.Message)") }
    }
    if ($null -eq $runFailure -and $cleanupErrors.Count -gt 0 -and $null -ne $artifactRoot) {
        try {
            Save-E2eFailureDiagnostics $repositoryRoot $artifactRoot $runRoot $postgresPassword ($cleanupErrors -join ' | ')
        }
        catch { $cleanupErrors.Add("Post-cleanup diagnostics: $($_.Exception.Message)") }
    }
    if ($null -eq $runFailure -and $cleanupErrors.Count -eq 0 -and $testPassed -and $null -ne $artifactRoot -and -not $keepSuccessfulArtifacts) {
        try { Remove-E2eManagedRunDirectory $repositoryRoot $artifactManagedRoot $runGuid $artifactRoot }
        catch { $cleanupErrors.Add("Successful-run artifact cleanup (manual review path '$artifactRoot'): $($_.Exception.Message)") }
    }
    Restore-ProcessEnvironment $previousEnvironment
    if ($null -ne $mutex) {
        try { Exit-E2eRunMutex $mutex }
        catch { $cleanupErrors.Add("Mutex release: $($_.Exception.Message)") }
    }
}

if ($null -ne $runFailure -or $cleanupErrors.Count -gt 0) {
    if ($null -ne $artifactRoot) { Write-Host "Failure diagnostics preserved at: $artifactRoot" }
    $parts = [Collections.Generic.List[string]]::new()
    if ($null -ne $runFailure) { $parts.Add("E2E failed: $($runFailure.Exception.Message)") }
    if ($cleanupErrors.Count -gt 0) { $parts.Add("Cleanup/diagnostics errors: $($cleanupErrors -join ' | ')") }
    throw ($parts -join [Environment]::NewLine)
}

Write-Host 'All Playwright demo E2E scenarios passed.'
if ($keepSuccessfulArtifacts) { Write-Host "Successful synthetic UI artifacts retained for visual review: $artifactRoot" }
Write-Host "Cleanup removed only project '$projectName' containers/network and run '$($runGuid.ToString('D'))'; volume '$databaseVolumeName' was preserved."
Write-Host "Synthetic identity source/journal volumes with prefix '$projectName-identity-$($runGuid.ToString('N'))' were preserved."
Write-Host 'Regular project reawote and demo project/volume state remained unchanged.'
