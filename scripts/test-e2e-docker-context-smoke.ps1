# Read-only integration smoke for the same Windows PowerShell path used by the
# E2E runner. It does not invoke Compose, create containers or change context.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
. (Join-Path $PSScriptRoot 'e2e-runner-operations.ps1')

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Write-Host 'SKIP: Windows Docker-context smoke requires Windows; 0 passed, 1 skipped, 0 failed.'
    exit 0
}

$script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
Start-E2eDiagnosticOperation -State $script:E2eDiagnostics -Phase 'safety_preflight' -OperationId 'repository_validation'
Complete-E2eDiagnosticPhase -State $script:E2eDiagnostics
Start-E2eDiagnosticOperation -State $script:E2eDiagnostics -Phase 'docker_context_validation' -OperationId 'docker_executable_resolution'

try {
    # Absence of the actual application is the only Docker-related skip. A
    # present CLI with a failed process, bad output, unavailable daemon or unsafe
    # context must fail; mocks are intentionally not used by this test.
    $applications = @(Microsoft.PowerShell.Core\Get-Command -Name 'docker.exe' -CommandType Application -ErrorAction SilentlyContinue)
    if ($applications.Count -eq 0) {
        Write-Host 'SKIP: docker.exe is not available; Windows Docker-context smoke: 0 passed, 1 skipped, 0 failed.'
        exit 0
    }
    $applications = $null
    # Keep even unexpected helper output private. The only successful public
    # output is the fixed result below, never a context name or endpoint.
    $privateOutput = @(Assert-LocalDockerContext 2>&1 3>&1 4>&1 5>&1 6>&1)
    Complete-E2eDiagnosticPhase -State $script:E2eDiagnostics
}
catch {
    if ($null -eq $script:E2eDiagnostics.failure_snapshot) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'unknown_safe_failure' -ExitCode $null
    }
    Write-Host (ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode 'E2E_ISOLATION_FAILED')
    Write-Host 'FAIL: Windows Docker-context smoke: 0 passed, 0 skipped, 1 failed.'
    exit 1
}
finally {
    $applications = $null
    $privateOutput = $null
}

Write-Host 'PASS: actual docker.exe through Assert-LocalDockerContext and Invoke-E2eReadCommand; Windows Docker-context smoke: 1 passed, 0 skipped, 0 failed.'
exit 0
