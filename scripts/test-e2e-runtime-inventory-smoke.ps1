# Read-only: inspect the real host using the runner's exact inventory and policy.
# No Compose, container lifecycle, volume mutation or fixture write is performed.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

$script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
Start-E2eRunnerPhase -Phase 'safety_preflight' -OperationId 'repository_validation'
try {
    $applications = @(Microsoft.PowerShell.Core\Get-Command -Name 'docker.exe' -CommandType Application -ErrorAction SilentlyContinue)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $applications.Count -eq 0) {
        Write-Host 'SKIP: Windows docker.exe is unavailable; runtime inventory smoke: 0 passed, 1 skipped, 0 failed.'
        exit 0
    }
    $repositoryRoot = Assert-LocalE2eRepositoryRoot -RepositoryRoot (Split-Path -Parent $PSScriptRoot)
    # Loading only function definitions cannot execute the write-capable runner.
    $tokens = $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'test-demo-e2e.ps1'), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -ne 0) { throw 'E2E_RUNNER_SOURCE_INVALID' }
    foreach ($statement in $ast.EndBlock.Statements) {
        if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
            . ([scriptblock]::Create($statement.Extent.Text))
        }
    }
    $script:E2eProjectName = 'reawote-e2e'
    $script:E2eDatabaseVolumeName = 'reawote-e2e-postgres-data'
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'docker_context_validation' -OperationId 'docker_executable_resolution'
    $privateOutput = @(Assert-LocalDockerContext 2>&1 3>&1 4>&1 5>&1 6>&1)
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'previous_runtime_cleanup' -OperationId 'runtime_inventory'
    # Intentionally never call Invoke-E2eRuntimeCleanup from this smoke.
    $privateOutput = @(Get-E2eRuntimeInventory 2>&1 3>&1 4>&1 5>&1 6>&1)
    Complete-E2eRunnerPhase
}
catch {
    if ($null -eq $script:E2eDiagnostics.failure_snapshot) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'unknown_safe_failure'
    }
    Write-Host (ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode 'E2E_ISOLATION_FAILED')
    Write-Host 'FAIL: runtime inventory smoke: 0 passed, 0 skipped, 1 failed.'
    exit 1
}
finally { $applications = $privateOutput = $null }
Write-Host 'PASS: real read-only runtime inventory accepted; 1 passed, 0 skipped, 0 failed. No Docker mutation was attempted.'
exit 0
