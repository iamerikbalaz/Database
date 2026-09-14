$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'e2e-private-process.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
. (Join-Path $PSScriptRoot 'e2e-runner-operations.ps1')

$realPrivateProcess = (Get-Command Invoke-E2ePrivateProcess).ScriptBlock
$nodeApplication = (Get-Command node -CommandType Application -ErrorAction Stop).Source
$script:PrivateRunnerSecret = 'synthetic-private-runner-secret-' + [guid]::NewGuid().ToString('N')
$script:PrivateRunnerCase = ''
$script:PrivateRunnerCalls = 0
$script:PrivateRunnerInput = $null
$passed = 0
$actualProcesses = 0

function Assert-PrivateRunnerRegression {
    param([bool] $Condition)
    if (-not $Condition) { throw 'E2E_PRIVATE_RUNNER_REGRESSION_FAILED' }
}

function Get-Location {
    param($ErrorAction)
    if ($script:PrivateRunnerCase -eq 'location_throw') { throw $script:PrivateRunnerSecret }
    Microsoft.PowerShell.Management\Get-Location -ErrorAction Stop
}

function Invoke-E2ePrivateProcess {
    param($FilePath, $Arguments, $StandardInput, $WorkingDirectory, $TimeoutMilliseconds, $OnStarted)
    $script:PrivateRunnerCalls++
    $script:PrivateRunnerInput = $StandardInput
    if ($script:PrivateRunnerCase.StartsWith('real_')) {
        return & $realPrivateProcess @PSBoundParameters
    }
    switch ($script:PrivateRunnerCase) {
        'throw' { throw $script:PrivateRunnerSecret }
        'bootstrap_throw' { throw $script:PrivateRunnerSecret }
        'no_output' { return }
        'malformed' { return [pscustomobject]@{ started = $true; completed = $true; exit_code = $script:PrivateRunnerSecret; error_category = $null } }
        'multiple' {
            [pscustomobject]@{ started = $true; completed = $true; exit_code = 0; error_category = $null }
            [pscustomobject]@{ started = $true; completed = $true; exit_code = 0; error_category = $null }
            return
        }
        'stdout' { Write-Output $script:PrivateRunnerSecret }
        'warning' { Write-Warning $script:PrivateRunnerSecret }
        'error_stream' { Write-Error $script:PrivateRunnerSecret -ErrorAction Continue }
        'nonzero' { return [pscustomobject]@{ started = $true; completed = $true; exit_code = 17; error_category = 'nonzero_exit' } }
        'already_classified' {
            Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'nonzero_exit' -ProcessStarted $true -ExitCode 23
            throw $script:PrivateRunnerSecret
        }
    }
    return [pscustomobject]@{
        started = $true; completed = $true; exit_code = 0; error_category = $null
        stdout = $script:PrivateRunnerSecret; stderr = $script:PrivateRunnerSecret; environment = $script:PrivateRunnerSecret
    }
}

function Invoke-E2ePrivateBootstrap {
    param($FilePath, $Arguments, $StandardInput, $WorkingDirectory, $TimeoutMilliseconds, $OnStarted)
    Invoke-E2ePrivateProcess @PSBoundParameters
}

$cases = @(
    @{ Name = 'real_default'; Category = $null; Started = $true; Exit = 0 },
    @{ Name = 'real_empty_stdin'; Category = $null; Started = $true; Exit = 0; Input = '' },
    @{ Name = 'real_nonempty_stdin'; Category = $null; Started = $true; Exit = 0; Input = 'synthetic stdin payload' },
    @{ Name = 'real_nonzero'; Category = 'nonzero_exit'; Started = $true; Exit = 17 },
    @{ Name = 'throw'; Category = 'unknown_safe_failure'; Started = $null; Exit = $null },
    @{ Name = 'bootstrap_throw'; Category = 'unknown_safe_failure'; Started = $null; Exit = $null },
    @{ Name = 'location_throw'; Category = 'invalid_state'; Started = $null; Exit = $null },
    @{ Name = 'no_output'; Category = 'invalid_state'; Started = $false; Exit = $null },
    @{ Name = 'malformed'; Category = 'invalid_state'; Started = $false; Exit = $null },
    @{ Name = 'multiple'; Category = 'invalid_state'; Started = $false; Exit = $null },
    @{ Name = 'stdout'; Category = 'invalid_state'; Started = $false; Exit = $null },
    @{ Name = 'warning'; Category = 'invalid_state'; Started = $false; Exit = $null },
    @{ Name = 'error_stream'; Category = 'invalid_state'; Started = $false; Exit = $null },
    @{ Name = 'nonzero'; Category = 'nonzero_exit'; Started = $true; Exit = 17 },
    @{ Name = 'already_classified'; Category = 'nonzero_exit'; Started = $true; Exit = 23 },
    @{ Name = 'safe_extra_fields'; Category = $null; Started = $true; Exit = 0 }
)

foreach ($case in $cases) {
    $script:PrivateRunnerCase = $case.Name
    $script:PrivateRunnerCalls = 0
    $script:PrivateRunnerInput = $null
    $script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
    Start-E2eRunnerPhase -Phase 'docker_context_validation' -OperationId 'docker_context_validation'
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'compose_config_validation' -OperationId 'compose_config_validation'
    $parameters = @{
        FilePath = $nodeApplication; Arguments = @('--version'); OperationId = 'previous_runtime_cleanup'
    }
    if ($case.ContainsKey('Input')) { $parameters.StandardInput = $case.Input }
    if ($case.Name -eq 'bootstrap_throw') { $parameters.Bootstrap = $true }
    if ($case.Name -eq 'real_nonempty_stdin') {
        $parameters.Arguments = @('-e', 'let input="";process.stdin.setEncoding("utf8");process.stdin.on("data",chunk=>input+=chunk);process.stdin.on("end",()=>process.exit(input==="synthetic stdin payload"?0:99))')
    }
    if ($case.Name -eq 'real_nonzero') { $parameters.Arguments = @('-e', 'process.exit(17)') }
    $capture = [Collections.Generic.List[object]]::new()
    $failure = $null
    try {
        & { Invoke-E2eRunnerPrivateProcess @parameters } 2>&1 3>&1 4>&1 5>&1 6>&1 |
            ForEach-Object { $capture.Add($_) }
    }
    catch { $failure = $_.Exception.Message }
    Assert-PrivateRunnerRegression ($capture.Count -eq 0)
    Assert-PrivateRunnerRegression ($script:E2eDiagnostics.operation_id -ceq 'previous_runtime_cleanup')
    Assert-PrivateRunnerRegression ($script:E2eDiagnostics.error_category -ceq $case.Category)
    Assert-PrivateRunnerRegression ($script:E2eDiagnostics.process_started -ceq $case.Started)
    Assert-PrivateRunnerRegression ($script:E2eDiagnostics.exit_code -ceq $case.Exit)
    Assert-PrivateRunnerRegression ($null -eq $script:E2eDiagnostics.system_error_code)
    if ($null -eq $case.Category) {
        Assert-PrivateRunnerRegression ($null -eq $failure -and $null -eq $script:E2eDiagnostics.failure_snapshot)
    }
    else {
        Assert-PrivateRunnerRegression ($failure -ceq 'E2E_SAFE_OPERATION_FAILED')
        Assert-PrivateRunnerRegression ($script:E2eDiagnostics.failure_snapshot.error_category -ceq $case.Category)
        Assert-PrivateRunnerRegression ($script:E2eDiagnostics.failure_snapshot.operation_id -ceq 'previous_runtime_cleanup')
    }
    if ($case.Name -eq 'location_throw') {
        Assert-PrivateRunnerRegression ($script:PrivateRunnerCalls -eq 0)
    }
    else {
        Assert-PrivateRunnerRegression ($script:PrivateRunnerCalls -eq 1)
        $expectedInput = if ($case.ContainsKey('Input')) { $case.Input } else { '' }
        Assert-PrivateRunnerRegression ($script:PrivateRunnerInput -ceq $expectedInput)
    }
    $safeText = ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode 'E2E_ISOLATION_FAILED'
    $keys = @($safeText -split '\r?\n' | ForEach-Object { ($_ -split '=', 2)[0] })
    Assert-PrivateRunnerRegression (($keys -join ',') -ceq 'schema_version,error_code,last_completed_phase,current_phase,failed_phase,operation_id,error_category,process_started,exit_code,system_error_code,log_check_status')
    Assert-PrivateRunnerRegression (-not $safeText.Contains($script:PrivateRunnerSecret))
    Assert-PrivateRunnerRegression (-not $safeText.Contains($nodeApplication))
    Assert-PrivateRunnerRegression (-not ($safeText -match 'stdout|stderr|environment|StandardInput|ProviderPath|Exception'))
    if ($case.Name.StartsWith('real_')) { $actualProcesses++ }
    $passed++
}

Write-Host "E2E private runner: $passed passed, 0 skipped, 0 failed; $actualProcesses real Node processes; no Docker commands."
