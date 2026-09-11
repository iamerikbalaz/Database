$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

$context = Get-DemoContext
Assert-DockerAvailable
$urls = Get-DemoUrls $context

Invoke-DemoCompose `
    -Context $context `
    -Arguments @("ps") `
    -Step "Demo service status"

foreach ($check in @(
    @{ Name = "backend"; Uri = "$($urls.Backend)/health" },
    @{ Name = "worker"; Uri = "$($urls.Worker)/health" },
    @{ Name = "frontend"; Uri = $urls.Frontend }
)) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $check.Uri -TimeoutSec 5
        Write-Host "$($check.Name): HTTP $($response.StatusCode) - $($check.Uri)"
    }
    catch {
        Write-Warning "$($check.Name) is unavailable at $($check.Uri): $($_.Exception.Message)"
    }
}

$workerId = & docker compose `
    --project-name $context.ProjectName `
    --env-file $context.EnvFile `
    -f $context.BaseCompose `
    -f $context.DemoCompose `
    ps --quiet worker
if ($LASTEXITCODE -ne 0) {
    throw "Could not identify the demo worker container."
}
if (-not [string]::IsNullOrWhiteSpace($workerId)) {
    $inspectionJson = (& docker inspect $workerId) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the demo worker container."
    }
    $inspection = ($inspectionJson | ConvertFrom-Json)[0]
    $materialsMount = @(
        @($inspection.Mounts) | Where-Object {
            $_.Destination -eq "/demo-materials"
        }
    )
    if ($materialsMount.Count -ne 1 -or $materialsMount[0].RW -ne $false) {
        throw "Worker demo materials mount is missing or is not read-only."
    }
    Write-Host "worker mount: read-only $($materialsMount[0].Source) -> /demo-materials"
}

& docker volume inspect "reawote-demo-postgres-data" *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "database volume: reawote-demo-postgres-data (present)"
}
else {
    Write-Warning "database volume: reawote-demo-postgres-data (not present)"
}
