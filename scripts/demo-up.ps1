$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

$context = Get-DemoContext
Assert-DockerAvailable

New-Item -ItemType Directory -Path $context.MaterialsRoot -Force | Out-Null

Invoke-DemoBaseCompose `
    -Context $context `
    -Arguments @("config", "--quiet") `
    -Step "Base Docker Compose configuration validation"

Invoke-DemoCompose `
    -Context $context `
    -Arguments @("config", "--quiet") `
    -Step "Demo Docker Compose configuration validation"

Invoke-DemoCompose `
    -Context $context `
    -Arguments @("up", "--build", "--detach", "--wait") `
    -Step "Demo environment startup"

# Backend startup already applies migrations. Running the command again proves that
# the current Alembic head is reached idempotently before seeding.
Invoke-DemoCompose `
    -Context $context `
    -Arguments @("exec", "-T", "backend", "alembic", "upgrade", "head") `
    -Step "Demo Alembic upgrade"

$urls = Get-DemoUrls $context
Write-Host "REAWOTE demo is running as Compose project '$($context.ProjectName)'."
Write-Host "Frontend:    $($urls.Frontend)"
Write-Host "Backend API: $($urls.Backend)/docs"
Write-Host "Backend:     $($urls.Backend)/health"
Write-Host "Worker:      $($urls.Worker)/health"
Write-Host ""
Write-Host "Seed or verify the demo data next: .\scripts\demo-seed.ps1"
