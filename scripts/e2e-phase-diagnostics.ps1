# Only fixed identifiers and primitive process status may leave this module.
# Never retain an exception, command line, process output or request payload here.
$script:E2eDiagnosticPhases = @(
    'safety_preflight', 'docker_context_validation', 'compose_config_validation',
    'database_start', 'database_readiness', 'database_runtime_validation',
    'database_schema_reset', 'browser_installation', 'image_build',
    'application_start', 'application_readiness', 'administrator_bootstrap',
    'fixture_seed', 'playwright_start', 'playwright_execution', 'cleanup'
)
$script:E2eDiagnosticOperations = @(
    'repository_validation', 'node_version_check', 'docker_context_validation',
    'compose_config_render', 'compose_config_validation', 'protected_state_check',
    'resource_ownership_check', 'volume_inspection', 'container_inspection',
    'network_inspection', 'runtime_resources_check', 'previous_runtime_cleanup',
    'docker_database_start', 'docker_database_readiness', 'database_mount_validation',
    'postgres_schema_reset', 'browser_installation', 'docker_image_build',
    'docker_application_start', 'docker_application_readiness', 'administrator_bootstrap',
    'fixture_seed', 'playwright_start', 'playwright_execution', 'runtime_log_scan',
    'cleanup_containers', 'cleanup_run_directory', 'cleanup_artifact_directory',
    'cleanup_environment', 'cleanup_mutex', 'diagnostics_write'
)
$script:E2eDiagnosticErrorCategories = @(
    'process_start', 'stdin_io', 'timeout', 'nonzero_exit', 'invalid_state',
    'validation_failure', 'unknown_safe_failure'
)
$script:E2eDiagnosticErrorCodes = @(
    'E2E_PREREQUISITE_FAILED', 'E2E_DOCKER_UNAVAILABLE', 'E2E_ISOLATION_FAILED',
    'E2E_START_FAILED', 'E2E_BOOTSTRAP_FAILED', 'E2E_SEED_FAILED',
    'E2E_PLAYWRIGHT_FAILED', 'E2E_LOG_SCAN_FAILED', 'E2E_CLEANUP_FAILED', 'E2E_SAFE_FAILURE'
)

function Get-E2eDiagnosticProperty {
    param($Value, [string] $Name)

    if ($null -eq $Value) { return $null }
    if ($Value -is [Collections.IDictionary]) {
        if ($Value.Contains($Name)) { return $Value[$Name] }
        return $null
    }
    if ($Value -isnot [pscustomobject]) { return $null }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -ne $property -and $property.MemberType -eq 'NoteProperty') {
        return $property.Value
    }
    return $null
}

function Get-E2eAllowedDiagnosticValue {
    param($Value, [string[]] $Allowed)

    if ($Value -is [string] -and $Allowed -ccontains $Value) { return $Value }
    return $null
}

function Get-E2eSafeDiagnosticExitCode {
    param($Value)
    # Windows native statuses may be represented as signed Int32 or unsigned
    # DWORD values parsed from JSON as Int64. Never coerce strings or objects.
    if (($Value -is [int] -or $Value -is [long] -or $Value -is [uint32]) -and
        [long]$Value -ge [int]::MinValue -and [long]$Value -le [uint32]::MaxValue) {
        return $Value
    }
    return $null
}

function New-E2ePhaseDiagnosticsState {
    return [pscustomobject]@{
        last_completed_phase = $null
        current_phase = $null
        operation_id = $null
        error_category = $null
        exit_code = $null
        log_check_status = 'not_run'
        failure_snapshot = $null
    }
}

function Start-E2eDiagnosticOperation {
    param([Parameter(Mandatory)] $State, $Phase, $OperationId)

    $safePhase = Get-E2eAllowedDiagnosticValue $Phase $script:E2eDiagnosticPhases
    $safeOperation = Get-E2eAllowedDiagnosticValue $OperationId $script:E2eDiagnosticOperations
    if ($null -eq $safePhase -or $null -eq $safeOperation) {
        throw 'E2E_DIAGNOSTIC_INPUT_REJECTED'
    }
    $State.current_phase = $safePhase
    $State.operation_id = $safeOperation
    $State.error_category = $null
    $State.exit_code = $null
}

function Complete-E2eDiagnosticPhase {
    param([Parameter(Mandatory)] $State)

    $phase = Get-E2eAllowedDiagnosticValue $State.current_phase $script:E2eDiagnosticPhases
    if ($null -eq $phase -or $null -ne $State.error_category) {
        throw 'E2E_DIAGNOSTIC_STATE_REJECTED'
    }
    $State.last_completed_phase = $phase
}

function Set-E2eDiagnosticFailure {
    param([Parameter(Mandatory)] $State, $ErrorCategory, $ExitCode = $null)

    $category = Get-E2eAllowedDiagnosticValue $ErrorCategory $script:E2eDiagnosticErrorCategories
    if ($null -eq $category) { $category = 'unknown_safe_failure' }
    $code = Get-E2eSafeDiagnosticExitCode $ExitCode
    $State.error_category = $category
    $State.exit_code = $code
    if ($null -eq $State.failure_snapshot) {
        $State.failure_snapshot = [pscustomobject]@{
            last_completed_phase = Get-E2eAllowedDiagnosticValue $State.last_completed_phase $script:E2eDiagnosticPhases
            current_phase = Get-E2eAllowedDiagnosticValue $State.current_phase $script:E2eDiagnosticPhases
            operation_id = Get-E2eAllowedDiagnosticValue $State.operation_id $script:E2eDiagnosticOperations
            error_category = $category
            exit_code = $code
        }
    }
}

function Set-E2eDiagnosticLogCheck {
    param([Parameter(Mandatory)] $State, $Status)

    $safeStatus = Get-E2eAllowedDiagnosticValue $Status @('passed', 'failed', 'not_run')
    if ($null -eq $safeStatus) { throw 'E2E_DIAGNOSTIC_INPUT_REJECTED' }
    $State.log_check_status = $safeStatus
}

function ConvertTo-E2eSafeProcessResult {
    param($Result)

    $started = Get-E2eDiagnosticProperty $Result 'started'
    $completed = Get-E2eDiagnosticProperty $Result 'completed'
    $exitCode = Get-E2eDiagnosticProperty $Result 'exit_code'
    $category = Get-E2eDiagnosticProperty $Result 'error_category'
    $valid = $started -is [bool] -and $completed -is [bool] -and
        ($null -eq $exitCode -or $null -ne (Get-E2eSafeDiagnosticExitCode $exitCode)) -and
        ($null -eq $category -or $null -ne (Get-E2eAllowedDiagnosticValue $category $script:E2eDiagnosticErrorCategories))
    if ($valid) {
        $valid = (-not $completed -or $started) -and
            ($completed -eq ($null -ne $exitCode)) -and
            ($null -ne $category -or ($started -and $completed -and $exitCode -eq 0)) -and
            ($category -ne 'nonzero_exit' -or ($completed -and $exitCode -ne 0)) -and
            ($category -ne 'process_start' -or (-not $started -and -not $completed))
    }
    if (-not $valid) {
        return [pscustomobject]@{
            started = $false; completed = $false; exit_code = $null; error_category = 'invalid_state'
        }
    }
    return [pscustomobject]@{
        started = $started; completed = $completed; exit_code = $exitCode; error_category = $category
    }
}

function ConvertTo-E2eSafeDiagnostics {
    param([Parameter(Mandatory)] $State, $ErrorCode = 'E2E_SAFE_FAILURE')

    $safeCode = Get-E2eAllowedDiagnosticValue $ErrorCode $script:E2eDiagnosticErrorCodes
    if ($null -eq $safeCode) { $safeCode = 'E2E_SAFE_FAILURE' }
    $failure = Get-E2eDiagnosticProperty $State 'failure_snapshot'
    $snapshot = if ($null -ne $failure) { $failure } else { $State }
    $lastCompleted = Get-E2eAllowedDiagnosticValue (Get-E2eDiagnosticProperty $snapshot 'last_completed_phase') $script:E2eDiagnosticPhases
    $current = Get-E2eAllowedDiagnosticValue (Get-E2eDiagnosticProperty $snapshot 'current_phase') $script:E2eDiagnosticPhases
    $failed = if ($null -ne $failure) { $current } else { $null }
    $operation = Get-E2eAllowedDiagnosticValue (Get-E2eDiagnosticProperty $snapshot 'operation_id') $script:E2eDiagnosticOperations
    $category = Get-E2eAllowedDiagnosticValue (Get-E2eDiagnosticProperty $snapshot 'error_category') $script:E2eDiagnosticErrorCategories
    $exitCode = Get-E2eDiagnosticProperty $snapshot 'exit_code'
    $exitCode = Get-E2eSafeDiagnosticExitCode $exitCode
    $logCheck = Get-E2eAllowedDiagnosticValue (Get-E2eDiagnosticProperty $State 'log_check_status') @('passed', 'failed', 'not_run')
    if ($null -eq $logCheck) { $logCheck = 'not_run' }
    $fields = [ordered]@{
        schema_version = 1
        error_code = $safeCode
        last_completed_phase = $lastCompleted
        current_phase = $current
        failed_phase = $failed
        operation_id = $operation
        error_category = $category
        exit_code = $exitCode
        log_check_status = $logCheck
    }
    return (($fields.GetEnumerator() | ForEach-Object {
        $safeValue = if ($null -eq $_.Value) { 'null' } else { [string]$_.Value }
        '{0}={1}' -f $_.Key, $safeValue
    }) -join [Environment]::NewLine)
}

function Invoke-E2eDiagnosticProcessOperation {
    param(
        [Parameter(Mandatory)] $State,
        $Phase,
        $OperationId,
        [Parameter(Mandatory)] [scriptblock] $Action,
        $ErrorCode = 'E2E_SAFE_FAILURE',
        [switch] $CompletePhase
    )

    Start-E2eDiagnosticOperation -State $State -Phase $Phase -OperationId $OperationId
    try {
        # Unexpected output is captured and rejected, never forwarded or serialized.
        $results = @(& $Action 2>&1 3>&1 4>&1 5>&1 6>&1)
        $result = if ($results.Count -eq 1) {
            ConvertTo-E2eSafeProcessResult -Result $results[0]
        }
        else {
            ConvertTo-E2eSafeProcessResult -Result $null
        }
    }
    catch {
        $result = [pscustomobject]@{
            started = $false; completed = $false; exit_code = $null; error_category = 'unknown_safe_failure'
        }
    }
    finally { $results = $null }
    if ($null -ne $result.error_category) {
        Set-E2eDiagnosticFailure -State $State -ErrorCategory $result.error_category -ExitCode $result.exit_code
        throw (ConvertTo-E2eSafeDiagnostics -State $State -ErrorCode $ErrorCode)
    }
    $State.exit_code = $result.exit_code
    if ($CompletePhase) { Complete-E2eDiagnosticPhase -State $State }
    return $result
}
