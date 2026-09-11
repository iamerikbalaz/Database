$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

$context = Get-DemoContext
Assert-DockerAvailable

Invoke-DemoCompose `
    -Context $context `
    -Arguments @("stop") `
    -Step "Demo environment shutdown"

Write-Host "REAWOTE demo containers are stopped."
Write-Host "The dedicated volume 'reawote-demo-postgres-data' was preserved."
Write-Host "Generated fixtures remain in '$($context.DemoDataRoot)'."
