$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')

function Assert-E2eDiagnosticTest {
    param([bool] $Condition)
    if (-not $Condition) { throw 'E2E_PHASE_DIAGNOSTICS_REGRESSION_FAILED' }
}

function New-ExpectedE2eDiagnosticText {
    param($Last, $Current, $Failed, $Operation, $Category, $Exit, $Log = 'not_run', $Code = 'E2E_START_FAILED',
        $ProcessStarted = $null, $SystemErrorCode = $null)
    $values = @('1', $Code, $Last, $Current, $Failed, $Operation, $Category, $ProcessStarted, $Exit, $SystemErrorCode, $Log)
    $keys = @('schema_version', 'error_code', 'last_completed_phase', 'current_phase',
        'failed_phase', 'operation_id', 'error_category', 'process_started', 'exit_code', 'system_error_code', 'log_check_status')
    $lines = for ($index = 0; $index -lt $keys.Count; $index++) {
        $value = if ($null -eq $values[$index]) { 'null' }
            elseif ($values[$index] -is [bool]) { $values[$index].ToString().ToLowerInvariant() }
            else { [string]$values[$index] }
        '{0}={1}' -f $keys[$index], $value
    }
    return $lines -join [Environment]::NewLine
}

$phaseOperations = [ordered]@{
    safety_preflight = 'repository_validation'
    docker_context_validation = 'docker_context_validation'
    compose_config_validation = 'compose_config_validation'
    previous_runtime_cleanup = 'runtime_inventory'
    database_start = 'docker_database_start'
    database_readiness = 'docker_database_readiness'
    database_runtime_validation = 'database_mount_validation'
    database_schema_reset = 'postgres_schema_reset'
    browser_installation = 'browser_installation'
    image_build = 'docker_image_build'
    application_start = 'docker_application_start'
    application_readiness = 'docker_application_readiness'
    administrator_bootstrap = 'administrator_bootstrap'
    fixture_seed = 'fixture_seed'
    playwright_start = 'playwright_start'
    playwright_execution = 'playwright_execution'
    cleanup = 'cleanup_containers'
}
$processCases = @(
    @{ Name = 'process_start'; Result = @{ started = $false; completed = $false; exit_code = $null; error_category = 'process_start' } },
    @{ Name = 'stdin_io'; Result = @{ started = $true; completed = $false; exit_code = $null; error_category = 'stdin_io' } },
    @{ Name = 'timeout'; Result = @{ started = $true; completed = $false; exit_code = $null; error_category = 'timeout' } },
    @{ Name = 'nonzero_exit'; Result = @{ started = $true; completed = $true; exit_code = 17; error_category = 'nonzero_exit' } },
    @{ Name = 'success'; Result = @{ started = $true; completed = $true; exit_code = 0; error_category = $null } }
)
$passed = 0
$secret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')

foreach ($phaseEntry in $phaseOperations.GetEnumerator()) {
    foreach ($case in $processCases) {
        $state = New-E2ePhaseDiagnosticsState
        Assert-E2eDiagnosticTest ($state.log_check_status -ceq 'not_run')
        Start-E2eDiagnosticOperation $state 'safety_preflight' 'repository_validation'
        Complete-E2eDiagnosticPhase $state
        $observation = [pscustomobject]@{ StartedBeforeAction = $false; CleanupRan = $false }
        $failure = $null
        $diagnosticBeforeCleanup = $null
        $mockResult = [pscustomobject]$case.Result
        # Extra properties must never become part of the returned result or diagnostics.
        $mockResult | Add-Member NoteProperty raw_exception $secret
        $mockResult | Add-Member NoteProperty stdout $secret
        $mockResult | Add-Member NoteProperty stderr $secret
        try {
            $safeResult = Invoke-E2eDiagnosticProcessOperation -State $state `
                -Phase $phaseEntry.Key -OperationId $phaseEntry.Value -ErrorCode 'E2E_START_FAILED' -CompletePhase -Action {
                    $observation.StartedBeforeAction = $state.current_phase -ceq $phaseEntry.Key -and
                        $state.operation_id -ceq $phaseEntry.Value -and
                        $state.last_completed_phase -ceq 'safety_preflight'
                    return $mockResult
                }
            Assert-E2eDiagnosticTest ($safeResult.PSObject.Properties.Name.Count -eq 4)
            $diagnosticBeforeCleanup = ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED'
        }
        catch {
            $failure = $_.Exception.Message
            $diagnosticBeforeCleanup = ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED'
        }
        finally {
            Start-E2eDiagnosticOperation $state 'cleanup' 'cleanup_containers'
            $observation.CleanupRan = $true
            Complete-E2eDiagnosticPhase $state
        }
        Assert-E2eDiagnosticTest $observation.StartedBeforeAction
        Assert-E2eDiagnosticTest $observation.CleanupRan
        if ($case.Name -eq 'success') {
            Assert-E2eDiagnosticTest ($null -eq $failure)
            $expected = New-ExpectedE2eDiagnosticText $phaseEntry.Key $phaseEntry.Key $null $phaseEntry.Value $null 0 -ProcessStarted $true
        }
        else {
            $expected = New-ExpectedE2eDiagnosticText 'safety_preflight' $phaseEntry.Key $phaseEntry.Key $phaseEntry.Value $case.Name $case.Result.exit_code -ProcessStarted $case.Result.started
            Assert-E2eDiagnosticTest ($failure -ceq $expected)
            Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
            # A later cleanup failure must not replace the original phase, category or exit.
            Set-E2eDiagnosticFailure $state 'validation_failure' 99
            Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
        }
        Assert-E2eDiagnosticTest ($diagnosticBeforeCleanup -ceq $expected)
        Assert-E2eDiagnosticTest (-not $diagnosticBeforeCleanup.Contains($secret))
        $passed++
        Write-Host ('PASS: phase={0}; result={1}' -f $phaseEntry.Key, $case.Name)
    }
}

# Exercise all other allowed categories, and keep log-check status live after failure.
foreach ($category in @('invalid_state', 'validation_failure', 'unknown_safe_failure',
    'executable_not_found', 'missing_exit_code', 'empty_output', 'invalid_output', 'policy_rejected')) {
    foreach ($status in @('passed', 'failed', 'not_run')) {
        $state = New-E2ePhaseDiagnosticsState
        Start-E2eDiagnosticOperation $state 'database_schema_reset' 'postgres_schema_reset'
        Set-E2eDiagnosticFailure $state $category
        Set-E2eDiagnosticLogCheck $state $status
        $expected = New-ExpectedE2eDiagnosticText $null 'database_schema_reset' 'database_schema_reset' 'postgres_schema_reset' $category $null $status
        $text = ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED'
        Assert-E2eDiagnosticTest ($text -ceq $expected -and -not $text.Contains($secret))
        $passed++
    }
}

# Invalid status and identifier values must not echo the rejected value.
foreach ($target in @('phase', 'operation', 'log_check')) {
    $state = New-E2ePhaseDiagnosticsState
    $failure = $null
    try {
        switch ($target) {
            'phase' { Start-E2eDiagnosticOperation $state $secret 'postgres_schema_reset' }
            'operation' { Start-E2eDiagnosticOperation $state 'database_schema_reset' $secret }
            'log_check' { Set-E2eDiagnosticLogCheck $state $secret }
        }
    }
    catch { $failure = $_.Exception.Message }
    Assert-E2eDiagnosticTest ($failure -ceq 'E2E_DIAGNOSTIC_INPUT_REJECTED')
    Assert-E2eDiagnosticTest (-not $failure.Contains($secret))
    $passed++
}

# Serialization revalidates even a tampered state and ignores every unknown field.
$state = New-E2ePhaseDiagnosticsState
$state.last_completed_phase = $secret
$state.current_phase = $secret
$state.operation_id = $secret
$state.error_category = $secret
$state.process_started = $secret
$state.exit_code = $secret
$state.system_error_code = $secret
$state.log_check_status = $secret
$state | Add-Member NoteProperty request_body $secret
$expected = New-ExpectedE2eDiagnosticText $null $null $null $null $null $null 'not_run' 'E2E_SAFE_FAILURE'
$text = ConvertTo-E2eSafeDiagnostics $state $secret
Assert-E2eDiagnosticTest ($text -ceq $expected -and -not $text.Contains($secret))
$passed++

# Unexpected emitted data, host/error streams and thrown values are captured privately.
foreach ($emission in @('output', 'warning', 'host', 'error', 'exception')) {
    $state = New-E2ePhaseDiagnosticsState
    $failure = $null
    $captured = @(& {
        try {
            [void](Invoke-E2eDiagnosticProcessOperation -State $state -Phase 'database_schema_reset' `
                -OperationId 'postgres_schema_reset' -ErrorCode 'E2E_START_FAILED' -Action {
                    switch ($emission) {
                        'output' { Write-Output $secret }
                        'warning' { Write-Warning $secret }
                        'host' { Write-Host $secret }
                        'error' { Write-Error $secret }
                        'exception' { throw $secret }
                    }
                    [pscustomobject]@{ started = $true; completed = $true; exit_code = 0; error_category = $null }
                })
        }
        catch { $_.Exception.Message }
    } 2>&1 3>&1 4>&1 5>&1 6>&1)
    $category = if ($emission -in @('error', 'exception')) { 'unknown_safe_failure' } else { 'invalid_state' }
    $expected = New-ExpectedE2eDiagnosticText $null 'database_schema_reset' 'database_schema_reset' 'postgres_schema_reset' $category $null -ProcessStarted $false
    Assert-E2eDiagnosticTest ($captured.Count -eq 1 -and [string]$captured[0] -ceq $expected)
    Assert-E2eDiagnosticTest (-not (($captured | Out-String).Contains($secret)))
    $passed++
}

# Badly shaped process results must not be misreported as successful completion.
foreach ($badResult in @(
    $null,
    @{ started = $false; completed = $true; exit_code = 0; error_category = $null },
    @{ started = $true; completed = $true; exit_code = $secret; error_category = $null },
    @{ started = $true; completed = $true; exit_code = 2; error_category = $null },
    @{ started = $secret; completed = $true; exit_code = 0; error_category = $null },
    @{ started = $true; completed = $false; exit_code = $null; error_category = $secret }
)) {
    $safe = ConvertTo-E2eSafeProcessResult $badResult
    Assert-E2eDiagnosticTest (-not $safe.started -and -not $safe.completed -and
        $null -eq $safe.exit_code -and $safe.error_category -ceq 'invalid_state')
    Assert-E2eDiagnosticTest (-not (($safe | ConvertTo-Json -Compress).Contains($secret)))
    $passed++
}

$secret = $null
foreach ($nativeCode in @([int]::MinValue, [long]3221225477, [uint32]::MaxValue)) {
    $safe = ConvertTo-E2eSafeProcessResult @{
        started = $true; completed = $true; exit_code = $nativeCode; error_category = 'nonzero_exit'
    }
    Assert-E2eDiagnosticTest ($safe.started -and $safe.completed -and $safe.exit_code -eq $nativeCode -and
        $safe.error_category -ceq 'nonzero_exit')
    $state = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $state 'database_schema_reset' 'postgres_schema_reset'
    Set-E2eDiagnosticFailure $state 'nonzero_exit' $nativeCode
    $expected = New-ExpectedE2eDiagnosticText $null 'database_schema_reset' 'database_schema_reset' 'postgres_schema_reset' 'nonzero_exit' $nativeCode
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
    $passed++
}
foreach ($invalidCode in @(([long][int]::MinValue - 1), ([long][uint32]::MaxValue + 1), '1', 1.5)) {
    Assert-E2eDiagnosticTest ($null -eq (Get-E2eSafeDiagnosticExitCode $invalidCode))
    $passed++
}

# Docker context sub-operations are fixed independently of the phase, and remain
# identifiable even after later cleanup changes all mutable state.
foreach ($operation in @('docker_executable_resolution', 'docker_context_show',
    'docker_context_inspect', 'docker_context_parse', 'docker_context_policy_validation')) {
    $state = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $state 'safety_preflight' 'repository_validation'
    Complete-E2eDiagnosticPhase $state
    Start-E2eDiagnosticOperation $state 'docker_context_validation' $operation
    Set-E2eDiagnosticFailure $state 'process_start' -ProcessStarted $false -SystemErrorCode 2
    $expected = New-ExpectedE2eDiagnosticText 'safety_preflight' 'docker_context_validation' `
        'docker_context_validation' $operation 'process_start' $null -ProcessStarted $false -SystemErrorCode 2
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
    Start-E2eDiagnosticOperation $state 'cleanup' 'cleanup_containers'
    Assert-E2eDiagnosticTest ($null -eq $state.process_started -and $null -eq $state.system_error_code)
    Set-E2eDiagnosticFailure $state 'nonzero_exit' 19 -ProcessStarted $true -SystemErrorCode 87
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
    $passed++
}

# Resource validation must identify its exact check, not imply that the already
# completed Compose configuration validation failed. Preserve these identifiers
# and null process status when later cleanup attempts fail independently.
foreach ($operation in @(
    'runtime_inventory', 'runtime_inventory_parse', 'runtime_project_validation',
    'container_ownership_validation', 'container_mount_validation',
    'network_ownership_validation', 'network_attachment_validation',
    'volume_name_validation', 'volume_driver_validation', 'volume_scope_validation',
    'volume_options_validation', 'volume_ownership_validation',
    'docker_mutation_executable_resolution', 'cleanup_resource_revalidation',
    'cleanup_container_remove', 'cleanup_network_remove', 'runtime_cleanup_verification'
)) {
    $state = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $state 'compose_config_validation' 'compose_config_validation'
    Complete-E2eDiagnosticPhase $state
    Start-E2eDiagnosticOperation $state 'previous_runtime_cleanup' $operation
    Set-E2eDiagnosticFailure $state 'validation_failure'
    $expected = New-ExpectedE2eDiagnosticText 'compose_config_validation' 'previous_runtime_cleanup' `
        'previous_runtime_cleanup' $operation 'validation_failure' $null -Code 'E2E_ISOLATION_FAILED'
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_ISOLATION_FAILED') -ceq $expected)
    Start-E2eDiagnosticOperation $state 'cleanup' 'cleanup_container_remove'
    Set-E2eDiagnosticFailure $state 'nonzero_exit' 17 -ProcessStarted $true
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_ISOLATION_FAILED') -ceq $expected)
    $passed++
}

# Strictly typed primitive status only: malformed values never enter state,
# failure snapshots, or final serialization; no string-to-number/bool coercion.
foreach ($invalidStarted in @('true', 'false', 'sensitive-cookie-or-csrf', 0, 1, @{}, [pscustomobject]@{ secret = 'sensitive' })) {
    $state = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $state 'docker_context_validation' 'docker_context_show'
    Set-E2eDiagnosticFailure $state 'missing_exit_code' -ProcessStarted $invalidStarted
    Assert-E2eDiagnosticTest ($null -eq $state.process_started -and $null -eq $state.failure_snapshot.process_started)
    $expected = New-ExpectedE2eDiagnosticText $null 'docker_context_validation' 'docker_context_validation' `
        'docker_context_show' 'missing_exit_code' $null
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
    $passed++
}
foreach ($invalidCode in @(([long][int]::MinValue - 1), ([long][uint32]::MaxValue + 1),
    '2', 2.5, $true, @{}, [pscustomobject]@{ secret = 'sensitive' })) {
    $state = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $state 'docker_context_validation' 'docker_context_show'
    Set-E2eDiagnosticFailure $state 'process_start' -ProcessStarted $false -SystemErrorCode $invalidCode
    Assert-E2eDiagnosticTest ($null -eq $state.system_error_code -and $null -eq $state.failure_snapshot.system_error_code)
    $state.failure_snapshot.system_error_code = $invalidCode
    $expected = New-ExpectedE2eDiagnosticText $null 'docker_context_validation' 'docker_context_validation' `
        'docker_context_show' 'process_start' $null -ProcessStarted $false
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
    $passed++
}
foreach ($systemCode in @([int]::MinValue, 0, 2, [long]3221225477, [uint32]::MaxValue)) {
    $state = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $state 'docker_context_validation' 'docker_context_inspect'
    Set-E2eDiagnosticFailure $state 'process_start' -ProcessStarted $false -SystemErrorCode $systemCode
    $expected = New-ExpectedE2eDiagnosticText $null 'docker_context_validation' 'docker_context_validation' `
        'docker_context_inspect' 'process_start' $null -ProcessStarted $false -SystemErrorCode $systemCode
    Assert-E2eDiagnosticTest ((ConvertTo-E2eSafeDiagnostics $state 'E2E_START_FAILED') -ceq $expected)
    $passed++
}

# Unknown data is discarded even when injected into a frozen failure snapshot.
$secret = [guid]::NewGuid().ToString('N')
$state = New-E2ePhaseDiagnosticsState
Start-E2eDiagnosticOperation $state 'docker_context_validation' 'docker_context_parse'
Set-E2eDiagnosticFailure $state 'invalid_output' 0 -ProcessStarted $true
foreach ($field in @('PATH', 'DOCKER_HOST', 'environment', 'arguments', 'stdout', 'stderr',
    'raw_exception', 'credentials', 'cookie', 'csrf_token', 'docker_endpoint')) {
    $state | Add-Member NoteProperty $field $secret
    $state.failure_snapshot | Add-Member NoteProperty $field $secret
}
$text = ConvertTo-E2eSafeDiagnostics $state 'E2E_ISOLATION_FAILED'
$keys = @($text -split [Environment]::NewLine | ForEach-Object { ($_ -split '=', 2)[0] })
$expectedKeys = @('schema_version', 'error_code', 'last_completed_phase', 'current_phase', 'failed_phase',
    'operation_id', 'error_category', 'process_started', 'exit_code', 'system_error_code', 'log_check_status')
Assert-E2eDiagnosticTest ($keys.Count -eq 11 -and ($keys -join '|') -ceq ($expectedKeys -join '|') -and -not $text.Contains($secret))
$passed++
$secret = $null
Write-Host ('E2E phase diagnostics tests: {0} passed, 0 skipped, 0 failed.' -f $passed)
