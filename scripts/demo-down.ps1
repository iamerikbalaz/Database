$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

$context = Get-DemoContext
$ports = Get-DemoPortConfiguration -Context $context
Assert-DockerAvailable

Invoke-DemoCompose `
    -Context $context `
    -Ports $ports `
    -Arguments @("stop") `
    -Step "Demo environment shutdown"

Write-Host "REAWOTE demo containers are stopped."
Write-Host "The dedicated volume 'reawote-demo-postgres-data' was preserved."
Write-Host "Generated fixtures remain in '$($context.DemoDataRoot)'."
