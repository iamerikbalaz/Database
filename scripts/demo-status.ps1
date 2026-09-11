$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

function Get-RequiredContainerInspection {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] $Ports,
        [Parameter(Mandatory)] [string] $Service
    )

    $containerIds = @(Invoke-DemoCompose `
        -Context $Context `
        -Ports $Ports `
        -Arguments @("ps", "--quiet", $Service) `
        -Step "Locate demo $Service container")
    $containerId = ($containerIds -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        throw "Required demo service '$Service' has no container."
    }

    $inspectionJson = (& docker inspect $containerId) -join [System.Environment]::NewLine
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the demo $Service container."
    }
    $inspection = @($inspectionJson | ConvertFrom-Json -ErrorAction Stop)
    if ($inspection.Count -ne 1) {
        throw "Expected exactly one inspected container for demo service '$Service'."
    }
    if ($inspection[0].Config.Labels.'com.docker.compose.project' -ne $Context.ProjectName) {
        throw "Container for '$Service' does not belong to '$($Context.ProjectName)'."
    }
    if ($inspection[0].Config.Labels.'com.docker.compose.service' -ne $Service) {
        throw "Inspected container does not belong to service '$Service'."
    }
    if ($inspection[0].State.Running -ne $true) {
        throw "Required demo service '$Service' is not running."
    }
    if ($inspection[0].State.Health.Status -ne "healthy") {
        throw "Required demo service '$Service' is not healthy."
    }
    return $inspection[0]
}

$context = Get-DemoContext
$ports = Get-DemoPortConfiguration -Context $context
$urls = Get-DemoUrls -Ports $ports
Assert-DockerAvailable

$renderedLines = @(Invoke-DemoCompose `
    -Context $context `
    -Ports $ports `
    -Arguments @("config", "--format", "json") `
    -Step "Rendered demo Docker Compose configuration validation")
Assert-DemoRenderedCompose `
    -Json ($renderedLines -join [System.Environment]::NewLine) `
    -Context $context `
    -Ports $ports

$inspections = @{}
foreach ($service in @("database", "backend", "worker", "frontend")) {
    $inspections[$service] = Get-RequiredContainerInspection `
        -Context $context `
        -Ports $ports `
        -Service $service
    Write-Host "$service container: running and healthy"
}

$expectedRuntimePorts = @{
    backend = @{ Key = "8000/tcp"; HostPort = [string]$ports.Backend }
    frontend = @{ Key = "5173/tcp"; HostPort = [string]$ports.Frontend }
    worker = @{ Key = "8080/tcp"; HostPort = [string]$ports.Worker }
}
foreach ($service in $inspections.Keys) {
    $portBindingsProperty = $inspections[$service].HostConfig.PSObject.Properties["PortBindings"]
    if ($null -eq $portBindingsProperty -or $null -eq $portBindingsProperty.Value) {
        if ($expectedRuntimePorts.ContainsKey($service)) {
            throw "Required demo service '$service' has no runtime port bindings."
        }
        continue
    }
    $allBindings = @()
    foreach ($bindingProperty in $portBindingsProperty.Value.PSObject.Properties) {
        foreach ($binding in @($bindingProperty.Value)) {
            if ($null -eq $binding) {
                continue
            }
            if ($binding.HostIp -ne "127.0.0.1") {
                throw "Service '$service' has a runtime port bound to forbidden host IP '$($binding.HostIp)'."
            }
            $allBindings += [pscustomobject]@{
                Key = $bindingProperty.Name
                HostPort = $binding.HostPort
            }
        }
    }
    if ($expectedRuntimePorts.ContainsKey($service)) {
        $expectedBinding = $expectedRuntimePorts[$service]
        $matches = @($allBindings | Where-Object {
            $_.Key -eq $expectedBinding.Key -and $_.HostPort -eq $expectedBinding.HostPort
        })
        if ($matches.Count -ne 1 -or $allBindings.Count -ne 1) {
            throw "Service '$service' does not have exactly one expected runtime loopback port binding."
        }
    }
    elseif ($allBindings.Count -ne 0) {
        throw "Service '$service' unexpectedly publishes a runtime port."
    }
}
Write-Host "runtime ports: all required bindings are exclusively on 127.0.0.1"

try {
    $backendHealth = Invoke-RestMethod -Method Get -Uri "$($urls.Backend)/health" -TimeoutSec 5
}
catch {
    throw "Backend health endpoint is unavailable: $($_.Exception.Message)"
}
if ($backendHealth.status -ne "ok" -or $backendHealth.database -ne "connected") {
    throw "Backend health endpoint did not report status=ok and database=connected."
}
Write-Host "backend health: status=ok, database=connected"

try {
    $workerHealth = Invoke-RestMethod -Method Get -Uri "$($urls.Worker)/health" -TimeoutSec 5
}
catch {
    throw "Worker health endpoint is unavailable: $($_.Exception.Message)"
}
if ($workerHealth.status -ne "ok" -or $workerHealth.service -ne "worker") {
    throw "Worker health endpoint returned an unexpected response."
}
Write-Host "worker health: status=ok"

try {
    $frontendResponse = Invoke-WebRequest -UseBasicParsing -Uri $urls.Frontend -TimeoutSec 5
}
catch {
    throw "Frontend endpoint is unavailable: $($_.Exception.Message)"
}
if ($frontendResponse.StatusCode -ne 200) {
    throw "Frontend endpoint returned HTTP $($frontendResponse.StatusCode), expected 200."
}
Write-Host "frontend: HTTP 200"

$workerMounts = @($inspections.worker.Mounts | Where-Object {
    $_.Destination -eq "/demo-materials"
})
if ($workerMounts.Count -ne 1) {
    throw "Worker must have exactly one /demo-materials mount."
}
$workerMount = $workerMounts[0]
if ($workerMount.Type -ne "bind") {
    throw "Worker /demo-materials mount must be a bind mount."
}
if ($workerMount.RW -ne $false) {
    throw "Worker /demo-materials mount is not read-only."
}
Write-Host "worker mount: trusted bind source, read-only"

$databaseMounts = @($inspections.database.Mounts | Where-Object {
    $_.Destination -eq "/var/lib/postgresql" -and
    $_.Type -eq "volume" -and
    $_.Name -eq $context.DatabaseVolumeName
})
if ($databaseMounts.Count -ne 1) {
    throw "Database is not using the dedicated demo PostgreSQL volume."
}

$volumeJson = (& docker volume inspect $context.DatabaseVolumeName) -join [System.Environment]::NewLine
if ($LASTEXITCODE -ne 0) {
    throw "Dedicated demo database volume '$($context.DatabaseVolumeName)' is missing."
}
$volumeInspection = @($volumeJson | ConvertFrom-Json -ErrorAction Stop)
if ($volumeInspection.Count -ne 1 -or $volumeInspection[0].Name -ne $context.DatabaseVolumeName) {
    throw "Dedicated demo database volume inspection returned an unexpected result."
}
Write-Host "database volume: $($context.DatabaseVolumeName)"

Write-Host "All required REAWOTE demo status and safety checks passed."
