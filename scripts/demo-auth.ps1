param(
    [string] $Email = "demo.admin@example.invalid",
    [string] $DisplayName = "Demo Administrator"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

if ([Console]::IsInputRedirected) {
    throw "Demo administrator provisioning requires an interactive terminal so the password is never piped or echoed."
}
if ([string]::IsNullOrWhiteSpace($Email) -or [string]::IsNullOrWhiteSpace($DisplayName)) {
    throw "Demo administrator email and display name must not be empty."
}

$context = Get-DemoContext
$ports = Get-DemoPortConfiguration -Context $context
Assert-DockerAvailable

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

Write-Host "Create the first administrator for the isolated REAWOTE demo."
Write-Host "The backend CLI will request the password twice without displaying it."
Invoke-DemoCompose `
    -Context $context `
    -Ports $ports `
    -Arguments @(
        "exec",
        "backend",
        "python",
        "-m",
        "app.auth.cli",
        "--email",
        $Email,
        "--display-name",
        $DisplayName
    ) `
    -Step "Demo administrator provisioning"

Write-Host "The demo administrator was created. No password was stored by this script."
