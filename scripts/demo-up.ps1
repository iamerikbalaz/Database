$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

$context = Get-DemoContext
$ports = Get-DemoPortConfiguration -Context $context
$urls = Get-DemoUrls -Ports $ports
Assert-DockerAvailable

[void](New-SafeDemoDirectory -Context $context -Path $context.MaterialsRoot)

Invoke-DemoBaseCompose `
    -Context $context `
    -Ports $ports `
    -Arguments @("config", "--quiet") `
    -Step "Base Docker Compose configuration validation"

$renderedLines = @(Invoke-DemoCompose `
    -Context $context `
    -Ports $ports `
    -Arguments @("config", "--format", "json") `
    -Step "Rendered demo Docker Compose configuration validation")
Assert-DemoRenderedCompose `
    -Json ($renderedLines -join [System.Environment]::NewLine) `
    -Context $context `
    -Ports $ports

$startupAttempted = $false
$startupComplete = $false
try {
    $startupAttempted = $true
    Invoke-DemoCompose `
        -Context $context `
        -Ports $ports `
        -Arguments @("up", "--build", "--detach", "--wait") `
        -Step "Demo environment startup"

    # Backend startup already applies migrations. Running the command again proves that
    # the current Alembic head is reached idempotently before seeding.
    Invoke-DemoCompose `
        -Context $context `
        -Ports $ports `
        -Arguments @("exec", "-T", "backend", "alembic", "upgrade", "head") `
        -Step "Demo Alembic upgrade"
    $startupComplete = $true
}
finally {
    if ($startupAttempted -and -not $startupComplete) {
        try {
            Invoke-DemoCompose `
                -Context $context `
                -Ports $ports `
                -Arguments @("down", "--remove-orphans") `
                -Step "Failed demo startup cleanup"
        }
        catch {
            Write-Error "Demo startup failed and project cleanup also failed: $($_.Exception.Message)"
        }
    }
}

Write-Host "REAWOTE demo is running as Compose project '$($context.ProjectName)'."
Write-Host "Frontend:    $($urls.Frontend)"
Write-Host "Backend API: $($urls.Backend)/docs"
Write-Host "Backend:     $($urls.Backend)/health"
Write-Host "Worker:      $($urls.Worker)/health"
Write-Host ""
Write-Host "Seed or verify the demo data next: .\scripts\demo-seed.ps1"
