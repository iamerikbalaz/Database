$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Start-E2eRunnerPhase {
    param([Parameter(Mandatory)] [string] $Phase, [Parameter(Mandatory)] [string] $OperationId)
    Start-E2eDiagnosticOperation -State $script:E2eDiagnostics -Phase $Phase -OperationId $OperationId
}

function Complete-E2eRunnerPhase {
    Complete-E2eDiagnosticPhase -State $script:E2eDiagnostics
}

function Set-E2eRunnerOperation {
    param([Parameter(Mandatory)] [string] $OperationId)
    Start-E2eDiagnosticOperation -State $script:E2eDiagnostics `
        -Phase $script:E2eDiagnostics.current_phase -OperationId $OperationId
}

function Invoke-E2eReadCommand {
    param(
        [Parameter(Mandatory)] [string] $Executable,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $OperationId
    )
    Set-E2eRunnerOperation -OperationId $OperationId
    # Only read-only metadata is returned internally for validation. stderr and
    # the original exception never enter a diagnostic or the caller's console.
    try {
        $application = (Get-Command $Executable -CommandType Application -ErrorAction Stop).Source
        $ErrorActionPreference = 'Continue'
        # A native launch error may be non-terminating in Windows PowerShell.
        # Do not mistake a previous process's exit code for this invocation.
        $global:LASTEXITCODE = $null
        $output = @(& $application @Arguments 2>$null)
        $nativeExit = $global:LASTEXITCODE
        $ErrorActionPreference = 'Stop'
    }
    catch {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'process_start' -ExitCode $null
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
    if ($null -eq $nativeExit) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'process_start' -ExitCode $null
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
    if ($nativeExit -ne 0) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'nonzero_exit' -ExitCode $nativeExit
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
    return $output
}

function Assert-E2ePrivateResult {
    param([Parameter(Mandatory)] $Result)
    $safe = ConvertTo-E2eSafeProcessResult -Result $Result
    if (-not $safe.started -or -not $safe.completed -or $safe.exit_code -ne 0 -or $null -ne $safe.error_category) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory $safe.error_category -ExitCode $safe.exit_code
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
}

function Invoke-E2eRunnerPrivateProcess {
    param(
        [Parameter(Mandatory)] [string] $FilePath,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $OperationId,
        [AllowEmptyString()] [string] $StandardInput = '',
        [string] $WorkingDirectory = (Get-Location).ProviderPath,
        [int] $TimeoutMilliseconds = 60000,
        [scriptblock] $OnStarted,
        [switch] $Bootstrap
    )
    Set-E2eRunnerOperation -OperationId $OperationId
    $parameters = @{
        FilePath = $FilePath; Arguments = $Arguments; StandardInput = $StandardInput
        WorkingDirectory = $WorkingDirectory; TimeoutMilliseconds = $TimeoutMilliseconds
    }
    if ($null -ne $OnStarted) { $parameters.OnStarted = $OnStarted }
    $result = if ($Bootstrap) { Invoke-E2ePrivateBootstrap @parameters } else { Invoke-E2ePrivateProcess @parameters }
    Assert-E2ePrivateResult -Result $result
}

function Invoke-E2eCleanupOperation {
    param([Parameter(Mandatory)] [string] $OperationId, [Parameter(Mandatory)] [scriptblock] $Action)
    Start-E2eDiagnosticOperation -State $script:E2eDiagnostics -Phase 'cleanup' -OperationId $OperationId
    try { & $Action }
    catch {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'unknown_safe_failure' -ExitCode $null
        $script:E2eCleanupFailed = $true
    }
}
