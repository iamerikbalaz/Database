$ErrorActionPreference = "Stop"

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

function Invoke-JsonPost {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [Parameter(Mandatory)] [hashtable]$Body
    )

    return Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri $Uri `
        -ContentType "application/json" `
        -Body ($Body | ConvertTo-Json -Depth 10 -Compress)
}

function Assert-HttpStatus {
    param(
        [Parameter(Mandatory)] $Response,
        [Parameter(Mandatory)] [int]$Expected,
        [Parameter(Mandatory)] [string]$Step
    )

    if ($Response.StatusCode -ne $Expected) {
        throw "$Step returned HTTP $($Response.StatusCode), expected $Expected."
    }
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repositoryRoot "docker-compose.yml"
$smokeRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("reawote-material-smoke-" + [guid]::NewGuid().ToString("N"))
$materialsRoot = Join-Path $smokeRoot "materials"
$overrideFile = Join-Path $smokeRoot "compose.smoke.yml"
$projectName = "reawote-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$backendPort = Get-FreeTcpPort
$workerPort = Get-FreeTcpPort
$prefix = "SMK" + [guid]::NewGuid().ToString("N").Substring(0, 8).ToUpperInvariant()
$identity = "${prefix}_0001_G03"
$wrongIdentity = "WRONG_0001_G03"
$folderPath = "library/$identity"
$wrongFolderPath = "library/$wrongIdentity"
$oldEnvironment = @{}
$environmentNames = @(
    "BACKEND_PORT",
    "COMPOSE_PROJECT_NAME",
    "MATERIALS_ROOT",
    "WORKER_BASE_URL"
)

foreach ($name in $environmentNames) {
    $oldEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is required for the Material Done smoke test."
}

New-Item -ItemType Directory -Path (Join-Path $materialsRoot "$folderPath/16K") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $materialsRoot "$wrongFolderPath/16K") | Out-Null
$mountSource = $materialsRoot.Replace("\", "/").Replace("'", "''")
$override = @"
services:
  worker:
    volumes:
      - type: bind
        source: '$mountSource'
        target: /materials
        read_only: true
    ports:
      - "127.0.0.1:${workerPort}:8080"
"@
[System.IO.File]::WriteAllText($overrideFile, $override)

try {
    $env:BACKEND_PORT = [string]$backendPort
    $env:COMPOSE_PROJECT_NAME = $projectName
    $env:MATERIALS_ROOT = "/materials"
    $env:WORKER_BASE_URL = "http://worker:8080"

    docker compose -f $composeFile -f $overrideFile up --build -d --wait backend worker
    if ($LASTEXITCODE -ne 0) {
        throw "Smoke-test Compose startup failed with exit code $LASTEXITCODE."
    }

    $backendBase = "http://127.0.0.1:$backendPort"
    $workerBase = "http://127.0.0.1:$workerPort"

    $workerPreflight = Invoke-JsonPost `
        -Uri "$workerBase/internal/material-preflight" `
        -Body @{ folder_path = $folderPath }
    Assert-HttpStatus $workerPreflight 200 "direct worker preflight"

    $user = Invoke-JsonPost `
        -Uri "$backendBase/api/internal-users" `
        -Body @{
            display_name = "Material smoke processor"
            email = "material-smoke-$([guid]::NewGuid().ToString('N'))@example.com"
            role = "PROCESSOR"
        }
    Assert-HttpStatus $user 201 "create user"
    $userBody = $user.Content | ConvertFrom-Json

    $company = Invoke-JsonPost `
        -Uri "$backendBase/api/companies" `
        -Body @{ name = "Material smoke company $([guid]::NewGuid().ToString('N'))" }
    Assert-HttpStatus $company 201 "create company"
    $companyBody = $company.Content | ConvertFrom-Json

    $project = Invoke-JsonPost `
        -Uri "$backendBase/api/projects" `
        -Body @{
            company_id = $companyBody.id
            project_number = "SMOKE-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
            name = "Material smoke project"
        }
    Assert-HttpStatus $project 201 "create project"
    $projectBody = $project.Content | ConvertFrom-Json

    $brand = Invoke-JsonPost `
        -Uri "$backendBase/api/brands" `
        -Body @{
            company_id = $companyBody.id
            name = "Material smoke brand"
            folder_prefix = $prefix
            brand_identifier = "material-smoke-$([guid]::NewGuid().ToString('N'))"
        }
    Assert-HttpStatus $brand 201 "create brand"
    $brandBody = $brand.Content | ConvertFrom-Json

    $material = Invoke-JsonPost `
        -Uri "$backendBase/api/materials" `
        -Body @{
            project_id = $projectBody.id
            published_brand_id = $brandBody.id
            material_name = "Material smoke stone"
            main_category_code = "G03"
            assigned_processor_id = $userBody.id
        }
    Assert-HttpStatus $material 201 "create material"
    $materialBody = $material.Content | ConvertFrom-Json
    if ($materialBody.technical_identity -ne $identity) {
        throw "Created identity $($materialBody.technical_identity) does not match $identity."
    }

    $preflight = Invoke-JsonPost `
        -Uri "$backendBase/api/materials/$($materialBody.id)/folder-preflight" `
        -Body @{ folder_path = $folderPath }
    Assert-HttpStatus $preflight 200 "backend folder-preflight"

    $wrongPreflight = Invoke-JsonPost `
        -Uri "$backendBase/api/materials/$($materialBody.id)/folder-preflight" `
        -Body @{ folder_path = $wrongFolderPath }
    Assert-HttpStatus $wrongPreflight 200 "identity-mismatch folder-preflight"
    $wrongBody = $wrongPreflight.Content | ConvertFrom-Json
    if ($wrongBody.identity_matches -ne $false -or $wrongBody.can_continue -ne $false) {
        throw "Identity mismatch did not return identity_matches=false and can_continue=false."
    }

    $link = Invoke-JsonPost `
        -Uri "$backendBase/api/materials/$($materialBody.id)/folder-link" `
        -Body @{ folder_path = $folderPath }
    Assert-HttpStatus $link 200 "folder-link"

    $done = Invoke-JsonPost `
        -Uri "$backendBase/api/materials/$($materialBody.id)/mark-done" `
        -Body @{}
    Assert-HttpStatus $done 200 "mark-done"
    $doneBody = $done.Content | ConvertFrom-Json
    if ($doneBody.material.workflow_status -ne "DONE") {
        throw "mark-done did not return workflow_status=DONE."
    }
    if ($done.Content.Contains("raw_content")) {
        throw "The public mark-done response contains raw_content."
    }

    [pscustomobject]@{
        direct_worker_preflight = $workerPreflight.StatusCode
        backend_folder_preflight = $preflight.StatusCode
        mismatch_folder_preflight = $wrongPreflight.StatusCode
        mismatch_identity_matches = $wrongBody.identity_matches
        mismatch_can_continue = $wrongBody.can_continue
        folder_link = $link.StatusCode
        mark_done = $done.StatusCode
        workflow_status = $doneBody.material.workflow_status
        public_raw_content_present = $done.Content.Contains("raw_content")
    } | ConvertTo-Json
}
finally {
    try {
        docker compose -f $composeFile -f $overrideFile down --volumes --remove-orphans
    }
    finally {
        foreach ($name in $environmentNames) {
            [System.Environment]::SetEnvironmentVariable(
                $name,
                $oldEnvironment[$name],
                "Process"
            )
        }
        if (Test-Path -LiteralPath $smokeRoot) {
            Remove-Item -LiteralPath $smokeRoot -Recurse -Force
        }
    }
}
