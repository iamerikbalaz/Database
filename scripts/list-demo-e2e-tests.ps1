$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

$repositoryRoot = Assert-LocalE2eRepositoryRoot (Split-Path -Parent $PSScriptRoot)
$runGuid = [guid]::NewGuid()
$runsRoot = Join-Path $repositoryRoot '.e2e-data\runs'
$artifactsRoot = Join-Path $repositoryRoot '.e2e-artifacts'
$runRoot = $artifactRoot = $null
$previousManifest = [Environment]::GetEnvironmentVariable('E2E_RUN_MANIFEST', 'Process')
$previousToken = [Environment]::GetEnvironmentVariable('E2E_RUN_TOKEN', 'Process')
$previousAuthEmail = [Environment]::GetEnvironmentVariable('E2E_AUTH_EMAIL', 'Process')
$previousAuthInitialPassword = [Environment]::GetEnvironmentVariable('E2E_AUTH_INITIAL_PASSWORD', 'Process')
$previousAuthPassword = [Environment]::GetEnvironmentVariable('E2E_AUTH_PASSWORD', 'Process')
try {
    $runRoot = New-E2eManagedRunDirectory $repositoryRoot $runsRoot $runGuid
    $artifactRoot = New-E2eManagedRunDirectory $repositoryRoot $artifactsRoot $runGuid
    $materialsRoot = New-E2eSafeDirectory $repositoryRoot $runRoot (Join-Path $runRoot 'materials')
    $token = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $fixture = [ordered]@{
        id = '00000000-0000-4000-8000-000000000001'
        technical_identity = 'E2E_SAFE_0001_G03'
        material_name = 'List-only synthetic material'
        folder_path = $null
        workflow_status = 'IN_PROGRESS'
        relativePath = 'e2e-library/E2E_SAFE_0001_G03'
    }
    $manifestPath = Join-Path $runRoot 'run-manifest.json'
    $manifest = [ordered]@{
        schemaVersion = 1; runGuid = $runGuid.ToString('D'); runnerTokenSha256 = Get-E2eSha256Hex $token
        frontendUrl = 'http://127.0.0.1:15173'; backendUrl = 'http://127.0.0.1:18000'
        frontendPort = 15173; backendPort = 18000; materialsRoot = $materialsRoot
        state = [ordered]@{
            companyId = '00000000-0000-4000-8000-000000000002'
            brandId = '00000000-0000-4000-8000-000000000003'
            projectId = '00000000-0000-4000-8000-000000000004'
            valid = $fixture; missing = $fixture; mismatch = $fixture
        }
    } | ConvertTo-Json -Depth 10
    Write-E2eSafeTextFile $repositoryRoot $runRoot $manifestPath $manifest
    $env:E2E_RUN_MANIFEST = $manifestPath
    $env:E2E_RUN_TOKEN = $token
    $env:E2E_AUTH_EMAIL = "e2e.admin.$($runGuid.ToString('N'))@example.invalid"
    $env:E2E_AUTH_INITIAL_PASSWORD = New-E2eSyntheticPassword
    do { $env:E2E_AUTH_PASSWORD = New-E2eSyntheticPassword } while (
        $env:E2E_AUTH_PASSWORD -eq $env:E2E_AUTH_INITIAL_PASSWORD
    )
    Push-Location (Join-Path $repositoryRoot 'frontend')
    try {
        & npm.cmd exec -- playwright test --list
        if ($LASTEXITCODE -ne 0) { throw "playwright test --list failed with exit code $LASTEXITCODE." }
    }
    finally { Pop-Location }
}
finally {
    [Environment]::SetEnvironmentVariable('E2E_RUN_MANIFEST', $previousManifest, 'Process')
    [Environment]::SetEnvironmentVariable('E2E_RUN_TOKEN', $previousToken, 'Process')
    [Environment]::SetEnvironmentVariable('E2E_AUTH_EMAIL', $previousAuthEmail, 'Process')
    [Environment]::SetEnvironmentVariable('E2E_AUTH_INITIAL_PASSWORD', $previousAuthInitialPassword, 'Process')
    [Environment]::SetEnvironmentVariable('E2E_AUTH_PASSWORD', $previousAuthPassword, 'Process')
    if ($null -ne $runRoot) { Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $runGuid $runRoot }
    if ($null -ne $artifactRoot) { Remove-E2eManagedRunDirectory $repositoryRoot $artifactsRoot $runGuid $artifactRoot }
}
