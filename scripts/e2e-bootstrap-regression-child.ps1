param(
    [Parameter(Mandatory)] [ValidateSet('echo-success', 'echo-failure', 'bootstrap-success', 'bootstrap-failure', 'workflow-failure', 'runner-failure')] [string] $Mode,
    [string] $RunRoot
)

# A subprocess fixture only; never used for production administrator bootstrap.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
$synthetic = [Console]::In.ReadToEnd().TrimEnd([char]13, [char]10)

if ($Mode -eq 'echo-success' -or $Mode -eq 'echo-failure') {
    if ([string]::IsNullOrEmpty($synthetic) -or @([Environment]::GetCommandLineArgs() | Where-Object { $_.Contains($synthetic) }).Count -ne 0) {
        exit 91
    }
    # Deliberately hostile stdout/stderr prove the private process wrapper
    # discards both streams, including fallback getpass warnings and prompts.
    [Console]::Out.WriteLine("raw stdout $synthetic")
    [Console]::Error.WriteLine("GetPassWarning: Password input may be echoed. Password: $synthetic")
    if ($Mode -eq 'echo-failure') { exit 23 }
    exit 0
}

$repositoryRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$artifact = Join-Path $RunRoot "$Mode.txt"
$safeResult = 'E2E_REGRESSION_OK'
foreach ($name in @(Get-E2eCredentialEnvironmentNames)) {
    [Environment]::SetEnvironmentVariable($name, $synthetic, 'Process')
}

try {
    if ($Mode -eq 'runner-failure') {
        # Exercise the actual runner's outer finally without Docker mutations,
        # even on a host which has Docker installed.
        function Get-Command {
            param([string] $Name, $CommandType, $ErrorAction)
            if ($Name -eq 'docker') { return $null }
            return Microsoft.PowerShell.Core\Get-Command -Name $Name -ErrorAction SilentlyContinue
        }
        & (Join-Path $PSScriptRoot 'test-demo-e2e.ps1')
    }
    else {
        Invoke-E2eWithCredentialCleanup -Action {
            if ($Mode -eq 'workflow-failure') {
                throw 'E2E_PLAYWRIGHT_FAILED: Synthetic browser workflow failed.'
            }
            $echoMode = if ($Mode -eq 'bootstrap-failure') { 'echo-failure' } else { 'echo-success' }
            $result = Invoke-E2ePrivateBootstrap -FilePath (Join-Path $PSHOME 'powershell.exe') -Arguments @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Mode', $echoMode
            ) -StandardInput $synthetic
            if (-not $result.started -or -not $result.completed -or
                $result.exit_code -ne 0 -or $null -ne $result.error_category) {
                $state = New-E2ePhaseDiagnosticsState
                Start-E2eDiagnosticOperation -State $state -Phase 'administrator_bootstrap' -OperationId 'administrator_bootstrap'
                $safeProcess = ConvertTo-E2eSafeProcessResult -Result $result
                Set-E2eDiagnosticFailure -State $state -ErrorCategory $safeProcess.error_category -ExitCode $safeProcess.exit_code
                throw (ConvertTo-E2eSafeDiagnostics -State $state -ErrorCode 'E2E_BOOTSTRAP_FAILED')
            }
        }
    }
}
catch {
    # This intentionally records the complete externally visible message so the
    # parent can detect a leak, but never emits a failed assertion's input.
    $safeResult = $_.Exception.Message
}

$remaining = @(@(Get-E2eCredentialEnvironmentNames) | Where-Object {
    $null -ne [Environment]::GetEnvironmentVariable($_, 'Process')
})
if ($remaining.Count -ne 0) {
    Clear-E2eCredentialEnvironment
    [Console]::Error.WriteLine('E2E_REGRESSION_ENVIRONMENT_NOT_CLEARED')
    exit 1
}
Write-E2eSafeTextFile -RepositoryRoot $repositoryRoot -RunRoot $RunRoot -Path $artifact -Content $safeResult
[Console]::Out.WriteLine($safeResult)
exit 0
