param(
    [Parameter(Mandatory)] [string] $Email,
    [Parameter(Mandatory)] [string] $DisplayName
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$context = Get-DemoContext
$ports = Get-DemoPortConfiguration $context
Assert-DockerAvailable
Invoke-DemoCompose -Context $context -Ports $ports -Arguments @(
    'exec', 'backend', 'python', '-m', 'app.auth.cli', '--email', $Email, '--display-name', $DisplayName
) -Step 'Interactive first demo administrator provisioning'
Write-Host 'Sign in at the demo frontend and change the initial password before running demo-seed.ps1.'
