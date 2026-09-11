$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

function Invoke-DemoApi {
    param(
        [Parameter(Mandatory)] [ValidateSet("GET", "POST")] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [hashtable] $Body
    )

    $parameters = @{
        UseBasicParsing = $true
        Method = $Method
        Uri = $script:BackendBase + $Path
        Headers = @{ Accept = "application/json" }
        TimeoutSec = 10
    }
    if ($null -ne $Body) {
        $parameters.ContentType = "application/json"
        $parameters.Body = $Body | ConvertTo-Json -Depth 10 -Compress
    }
    $response = Invoke-WebRequest @parameters
    return $response.Content | ConvertFrom-Json
}

function Get-OrCreateDemoRecord {
    param(
        [Parameter(Mandatory)] [string] $ListPath,
        [Parameter(Mandatory)] [scriptblock] $Match,
        [Parameter(Mandatory)] [string] $CreatePath,
        [Parameter(Mandatory)] [hashtable] $Body,
        [Parameter(Mandatory)] [string] $Label
    )

    $matches = @((Invoke-DemoApi -Method GET -Path $ListPath) | Where-Object $Match)
    if ($matches.Count -gt 1) {
        throw "More than one existing $Label matches the stable demo key. Refusing to guess."
    }
    if ($matches.Count -eq 1) {
        Write-Host "Reusing $Label $($matches[0].id)"
        return $matches[0]
    }
    $created = Invoke-DemoApi -Method POST -Path $CreatePath -Body $Body
    Write-Host "Created $Label $($created.id)"
    return $created
}

function Assert-RecordValue {
    param(
        [Parameter(Mandatory)] $Record,
        [Parameter(Mandatory)] [string] $Property,
        [Parameter(Mandatory)] $Expected,
        [Parameter(Mandatory)] [string] $Label
    )
    if ($Record.$Property -ne $Expected) {
        throw "$Label has unexpected $Property '$($Record.$Property)'; expected '$Expected'."
    }
}

function Write-Utf8Fixture {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Content
    )
    [System.IO.File]::WriteAllText(
        $Path,
        $Content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Invoke-WorkerPreflight {
    param(
        [Parameter(Mandatory)] [string] $WorkerBase,
        [Parameter(Mandatory)] [string] $FolderPath
    )

    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$WorkerBase/internal/material-preflight" `
        -ContentType "application/json" `
        -Headers @{ Accept = "application/json" } `
        -Body (@{ folder_path = $FolderPath } | ConvertTo-Json -Compress) `
        -TimeoutSec 10
    return $response.Content | ConvertFrom-Json
}

$context = Get-DemoContext
Assert-DockerAvailable
$urls = Get-DemoUrls $context
$script:BackendBase = $urls.Backend

try {
    $health = Invoke-RestMethod -Method Get -Uri "$script:BackendBase/health" -TimeoutSec 5
}
catch {
    throw "Demo backend is unavailable. Run .\scripts\demo-up.ps1 first. $($_.Exception.Message)"
}
if ($health.status -ne "ok" -or $health.database -ne "connected") {
    throw "Demo backend health did not report an available database."
}

$company = Get-OrCreateDemoRecord `
    -ListPath "/api/companies?search=Demo%20Company" `
    -Match { $_.name -eq "Demo Company" } `
    -CreatePath "/api/companies" `
    -Body @{
        name = "Demo Company"
        legal_name = "Demo Company s.r.o. (fictional)"
        country = "CZ"
        website = "https://demo.invalid"
        is_active = $true
    } `
    -Label "company"
Assert-RecordValue $company "is_active" $true "Company"

$brand = Get-OrCreateDemoRecord `
    -ListPath "/api/brands?search=demo-reawote-brand-v1" `
    -Match { $_.brand_identifier -eq "demo-reawote-brand-v1" } `
    -CreatePath "/api/brands" `
    -Body @{
        company_id = $company.id
        name = "DEMO Published Brand"
        folder_prefix = "DEMO_SAFE"
        brand_identifier = "demo-reawote-brand-v1"
        is_active = $true
    } `
    -Label "published brand"
Assert-RecordValue $brand "company_id" $company.id "Published brand"
Assert-RecordValue $brand "folder_prefix" "DEMO_SAFE" "Published brand"
Assert-RecordValue $brand "is_active" $true "Published brand"

$project = Get-OrCreateDemoRecord `
    -ListPath "/api/projects?search=DEMO-001" `
    -Match { $_.project_number -eq "DEMO-001" } `
    -CreatePath "/api/projects" `
    -Body @{
        company_id = $company.id
        project_number = "DEMO-001"
        name = "REAWOTE Internal Demo Project"
        status = "IN_PROGRESS"
        notes = "Fictional local-only data for the first internal REAWOTE demo."
    } `
    -Label "project"
Assert-RecordValue $project "company_id" $company.id "Project"

$processor = Get-OrCreateDemoRecord `
    -ListPath "/api/internal-users?search=demo.processor%40example.invalid" `
    -Match { $_.email -eq "demo.processor@example.invalid" } `
    -CreatePath "/api/internal-users" `
    -Body @{
        display_name = "Demo Processor"
        email = "demo.processor@example.invalid"
        role = "PROCESSOR"
        is_active = $true
    } `
    -Label "internal user"
Assert-RecordValue $processor "is_active" $true "Internal user"
Assert-RecordValue $processor "role" "PROCESSOR" "Internal user"

$materialDefinitions = @(
    @{ Key = "valid"; Name = "Demo Valid Metadata" },
    @{ Key = "missing"; Name = "Demo Missing Metadata" },
    @{ Key = "mismatch"; Name = "Demo Identity Mismatch" }
)
$materials = @{}
foreach ($definition in $materialDefinitions) {
    $encodedName = [System.Uri]::EscapeDataString($definition.Name)
    $material = Get-OrCreateDemoRecord `
        -ListPath "/api/materials?search=$encodedName" `
        -Match {
            $_.material_name -eq $definition.Name -and
            $_.project_id -eq $project.id -and
            $_.published_brand_id -eq $brand.id
        } `
        -CreatePath "/api/materials" `
        -Body @{
            project_id = $project.id
            published_brand_id = $brand.id
            material_name = $definition.Name
            main_category_code = "G03"
            assigned_processor_id = $processor.id
        } `
        -Label "material '$($definition.Key)'"
    Assert-RecordValue $material "assigned_processor_id" $processor.id "Material '$($definition.Key)'"
    Assert-RecordValue $material "main_category_code" "G03" "Material '$($definition.Key)'"
    $materials[$definition.Key] = $material
}

$libraryRoot = Join-Path $context.MaterialsRoot "demo-library"
$validFolder = Join-Path $libraryRoot $materials.valid.technical_identity
$missingFolder = Join-Path $libraryRoot $materials.missing.technical_identity
$mismatchFolderName = "DEMO_WRONG_FOLDER_G03"
$mismatchFolder = Join-Path $libraryRoot $mismatchFolderName
foreach ($folder in @($validFolder, $missingFolder, $mismatchFolder)) {
    New-Item -ItemType Directory -Path (Join-Path $folder "16K") -Force | Out-Null
    Write-Utf8Fixture `
        -Path (Join-Path $folder "16K/README.txt") `
        -Content "Demo-only empty master-resolution directory. No PBR maps are included.`n"
}

$validMetadata = @'
{
  "COLOR": {"hex": "#A1B2C3"},
  "TEXTURE_SIZE": {"cm": {"width": 12.5, "height": 34}}
}
'@
Write-Utf8Fixture -Path (Join-Path $validFolder "metadata.txt") -Content $validMetadata

foreach ($unexpectedMetadata in @(
    (Join-Path $missingFolder "metadata.txt"),
    (Join-Path $mismatchFolder "metadata.txt")
)) {
    if (Test-Path -LiteralPath $unexpectedMetadata) {
        throw "Fixture expected metadata.txt to be absent, but the file exists: $unexpectedMetadata"
    }
}

$relativePaths = @{
    valid = "demo-library/$($materials.valid.technical_identity)"
    missing = "demo-library/$($materials.missing.technical_identity)"
    mismatch = "demo-library/$mismatchFolderName"
}
$preflights = @{
    valid = Invoke-DemoApi -Method POST -Path "/api/materials/$($materials.valid.id)/folder-preflight" -Body @{ folder_path = $relativePaths.valid }
    missing = Invoke-DemoApi -Method POST -Path "/api/materials/$($materials.missing.id)/folder-preflight" -Body @{ folder_path = $relativePaths.missing }
    mismatch = Invoke-DemoApi -Method POST -Path "/api/materials/$($materials.mismatch.id)/folder-preflight" -Body @{ folder_path = $relativePaths.mismatch }
}
$workerPreflights = @{
    valid = Invoke-WorkerPreflight -WorkerBase $urls.Worker -FolderPath $relativePaths.valid
    missing = Invoke-WorkerPreflight -WorkerBase $urls.Worker -FolderPath $relativePaths.missing
    mismatch = Invoke-WorkerPreflight -WorkerBase $urls.Worker -FolderPath $relativePaths.mismatch
}
if (-not $preflights.valid.identity_matches -or -not $preflights.valid.can_continue -or $preflights.valid.metadata_status -ne "VALID") {
    throw "Valid fixture preflight returned an unexpected result."
}
if (-not $preflights.missing.identity_matches -or -not $preflights.missing.can_continue -or $preflights.missing.metadata_status -ne "MISSING") {
    throw "Missing-metadata fixture preflight returned an unexpected result."
}
if ($preflights.mismatch.identity_matches -or $preflights.mismatch.can_continue) {
    throw "Identity-mismatch fixture preflight did not fail as expected."
}
if (-not $workerPreflights.valid.can_continue -or $workerPreflights.valid.metadata.status -ne "VALID") {
    throw "Direct worker preflight rejected the valid fixture."
}
if (-not $workerPreflights.missing.can_continue -or $workerPreflights.missing.metadata.status -ne "MISSING") {
    throw "Direct worker preflight returned an unexpected missing-metadata result."
}
if (-not $workerPreflights.mismatch.can_continue -or $workerPreflights.mismatch.folder_name -ne $mismatchFolderName) {
    throw "Direct worker preflight rejected the safe identity-mismatch fixture unexpectedly."
}

$state = [ordered]@{
    compose_project = $context.ProjectName
    frontend_url = $urls.Frontend
    backend_docs_url = "$($urls.Backend)/docs"
    company = @{ id = $company.id; url = "$($urls.Frontend)/companies/$($company.id)" }
    published_brand = @{ id = $brand.id; url = "$($urls.Frontend)/brands/$($brand.id)" }
    project = @{ id = $project.id; url = "$($urls.Frontend)/projects/$($project.id)" }
    processor = @{ id = $processor.id; email = $processor.email; is_active = $processor.is_active }
    materials = [ordered]@{
        valid = @{
            id = $materials.valid.id
            technical_identity = $materials.valid.technical_identity
            folder_path = $relativePaths.valid
            workflow_status = $materials.valid.workflow_status
            url = "$($urls.Frontend)/materials/$($materials.valid.id)"
        }
        missing_metadata = @{
            id = $materials.missing.id
            technical_identity = $materials.missing.technical_identity
            folder_path = $relativePaths.missing
            workflow_status = $materials.missing.workflow_status
            url = "$($urls.Frontend)/materials/$($materials.missing.id)"
        }
        identity_mismatch = @{
            id = $materials.mismatch.id
            technical_identity = $materials.mismatch.technical_identity
            folder_path = $relativePaths.mismatch
            workflow_status = $materials.mismatch.workflow_status
            url = "$($urls.Frontend)/materials/$($materials.mismatch.id)"
        }
    }
    preflight = @{
        valid = @{ metadata_status = $preflights.valid.metadata_status; identity_matches = $preflights.valid.identity_matches; can_continue = $preflights.valid.can_continue }
        missing_metadata = @{ metadata_status = $preflights.missing.metadata_status; identity_matches = $preflights.missing.identity_matches; can_continue = $preflights.missing.can_continue }
        identity_mismatch = @{ metadata_status = $preflights.mismatch.metadata_status; identity_matches = $preflights.mismatch.identity_matches; can_continue = $preflights.mismatch.can_continue }
    }
    worker_preflight = @{
        valid = @{ metadata_status = $workerPreflights.valid.metadata.status; can_continue = $workerPreflights.valid.can_continue }
        missing_metadata = @{ metadata_status = $workerPreflights.missing.metadata.status; can_continue = $workerPreflights.missing.can_continue }
        identity_mismatch_fixture = @{ metadata_status = $workerPreflights.mismatch.metadata.status; can_continue = $workerPreflights.mismatch.can_continue }
    }
}
New-Item -ItemType Directory -Path $context.DemoDataRoot -Force | Out-Null
Write-Utf8Fixture -Path $context.StateFile -Content ($state | ConvertTo-Json -Depth 10)

Write-Host ""
Write-Host "Demo seed is ready and idempotent stable records were reused where present."
Write-Host "State file: $($context.StateFile)"
Write-Host "Company:         $($company.id)  $($state.company.url)"
Write-Host "Published brand: $($brand.id)  $($state.published_brand.url)"
Write-Host "Project:         $($project.id)  $($state.project.url)"
Write-Host "Processor:       $($processor.id)  $($processor.display_name) (active=$($processor.is_active))"
foreach ($key in @("valid", "missing_metadata", "identity_mismatch")) {
    $item = $state.materials[$key]
    Write-Host "$key material: $($item.id)  $($item.technical_identity)  $($item.folder_path)"
    Write-Host "  $($item.url)"
}
Write-Host "Swagger operations and metadata history: $($urls.Backend)/docs"
