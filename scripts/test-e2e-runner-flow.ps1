$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
. (Join-Path $PSScriptRoot 'e2e-runner-operations.ps1')

$runnerPath = Join-Path $PSScriptRoot 'test-demo-e2e.ps1'
$parseTokens = $parseErrors = $null
$runnerAst = [Management.Automation.Language.Parser]::ParseFile($runnerPath, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw 'E2E_RUNNER_FLOW_SOURCE_INVALID' }
$mainStatements = [Collections.Generic.List[string]]::new()
foreach ($statement in $runnerAst.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        # Load the real runner functions, including both stdin call sites.
        . ([scriptblock]::Create($statement.Extent.Text))
        continue
    }
    if ($statement -is [Management.Automation.Language.PipelineAst] -and
        $statement.PipelineElements[0] -is [Management.Automation.Language.CommandAst] -and
        $statement.PipelineElements[0].InvocationOperator -eq 'Dot') { continue }
    $mainStatements.Add($statement.Extent.Text)
}
# A scriptblock has no ExternalScriptInfo.Path. Substitute only this execution
# context lookup; every main-runner branch, phase transition and finally is real.
$mainBody = [scriptblock]::Create(($mainStatements -join [Environment]::NewLine).Replace(
    '$MyInvocation.MyCommand.Path', '$flowHarness.RunnerPath'
))
$realPhaseStart = (Get-Command Start-E2eRunnerPhase).ScriptBlock
$realPhaseComplete = (Get-Command Complete-E2eRunnerPhase).ScriptBlock
$realReadCommand = (Get-Command Invoke-E2eReadCommand).ScriptBlock
$actualNodeApplication = (Get-Command node -CommandType Application -ErrorAction Stop).Source

function Assert-FlowRegression {
    param([bool] $Condition)
    if (-not $Condition) { throw 'E2E_RUNNER_FLOW_REGRESSION_FAILED' }
}

function Start-E2eRunnerPhase {
    param([string] $Phase, [string] $OperationId)
    & $realPhaseStart -Phase $Phase -OperationId $OperationId
    $flowHarness.Phases.Add($Phase)
}
function Complete-E2eRunnerPhase {
    & $realPhaseComplete
    $flowHarness.Completed.Add($script:E2eDiagnostics.last_completed_phase)
}

function Invoke-FlowBoundary {
    param([string] $OperationId)
    if ($OperationId) { Set-E2eRunnerOperation -OperationId $OperationId }
    $flowHarness.Boundaries.Add($script:E2eDiagnostics.current_phase + ':' + $script:E2eDiagnostics.operation_id)
    if (-not $flowHarness.Injected -and $script:E2eDiagnostics.current_phase -ceq $flowHarness.FailPhase -and
        (-not $flowHarness.FailOperation -or $script:E2eDiagnostics.operation_id -ceq $flowHarness.FailOperation)) {
        $flowHarness.Injected = $true
        $flowHarness.ExpectedOperation = $script:E2eDiagnostics.operation_id
        $flowHarness.ExpectedLastCompleted = $script:E2eDiagnostics.last_completed_phase
        # The real result checker must preserve only the safe numeric status.
        Assert-E2ePrivateResult ([pscustomobject]@{
            started = $true; completed = $true; exit_code = 17; error_category = 'nonzero_exit'
            stdout = $flowHarness.Secret; stderr = $flowHarness.Secret
        })
    }
}

# All external boundaries are in-memory doubles. No Docker, child process, HTTP,
# directory creation, file write, deletion, browser or socket is invoked.
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    return [pscustomobject]@{ Source = $flowHarness.ResolvedApplication }
}
function Resolve-Path {
    param($LiteralPath)
    return [pscustomobject]@{ ProviderPath = $flowHarness.RunnerPath }
}
function Test-Path {
    param($LiteralPath, $PathType)
    if ($script:E2eDiagnostics.current_phase -eq 'playwright_start' -and -not $flowHarness.ProcessStartFailure) { Invoke-FlowBoundary }
    return -not $flowHarness.Removed.Contains([string]$LiteralPath)
}
function Assert-LocalE2eRepositoryRoot {
    param($RepositoryRoot)
    Invoke-FlowBoundary 'repository_validation'
    return $RepositoryRoot
}
function Assert-E2eNoReparsePath { param($Root, $Target) }
function Assert-LocalDockerContext {
    Invoke-FlowBoundary 'docker_context_validation'
    return $true
}
function Enter-E2eRunMutex { $flowHarness.MutexAcquired = $true; return [pscustomobject]@{ mock = $true } }
function Exit-E2eRunMutex { param($Mutex); $flowHarness.MutexCleaned = $true }
function Get-FreeTcpPort { $flowHarness.Port++; return $flowHarness.Port }
function New-E2eManagedRunDirectory {
    param($RepositoryRoot, $ManagedRoot, $RunGuid)
    $flowHarness.ManagedDirectoryCount++
    return Join-Path $ManagedRoot $RunGuid.ToString('D')
}
function New-E2eSafeDirectory { param($RepositoryRoot, $RunRoot, $Path); return $Path }
function Remove-E2eManagedRunDirectory {
    param($RepositoryRoot, $ManagedRoot, $RunGuid, $RunPath)
    [void]$flowHarness.Removed.Add($RunPath)
    $flowHarness.DirectoryCleanupCount++
}
function Write-E2eSafeTextFile {
    param($RepositoryRoot, $RunRoot, $Path, $Content)
    if ([IO.Path]::GetFileName($Path) -eq 'runner-diagnostics.txt') {
        $flowHarness.Diagnostics.Add([string]$Content)
    }
}
function Clear-E2eCredentialEnvironment {
    $flowHarness.CredentialCleanupCount++
    if ($null -ne $script:E2eDiagnostics -and $script:E2eDiagnostics.current_phase -eq 'cleanup') {
        Invoke-FlowBoundary 'cleanup_environment'
    }
    foreach ($name in @(Get-E2eCredentialEnvironmentNames)) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}
function Get-ProtectedState { Invoke-FlowBoundary 'protected_state_check'; return 'unchanged' }
function Assert-E2eResourcesOwned { Invoke-FlowBoundary 'resource_ownership_check' }
function Assert-RuntimeDatabaseMount { Invoke-FlowBoundary 'database_mount_validation' }
function Assert-E2eDatabaseEngineVolume {
    param([switch] $RequirePresent)
    Invoke-FlowBoundary 'volume_inspection'
}
function Assert-NoE2eRuntimeResources { Invoke-FlowBoundary 'runtime_resources_check' }
function Test-E2eRuntimeResourcesExist { return $true }
function Assert-RenderedE2eCompose { param($Json); Invoke-FlowBoundary 'compose_config_validation' }
function New-E2eSeedManifestData {
    param($BackendUrl, $RepositoryRoot, $RunRoot, $MaterialsRoot)
    Invoke-FlowBoundary 'fixture_seed'
    return @{ fixture = 'mock' }
}
function Invoke-E2eReadCommand {
    param($Executable, $Arguments, $OperationId)
    Invoke-FlowBoundary $OperationId
    if ($OperationId -eq 'node_version_check') { return 'v22.0.0' }
    if ($OperationId -eq 'runtime_log_scan') { return '' }
    return '{}'
}
function Invoke-E2ePrivateProcess {
    param($FilePath, $Arguments, $StandardInput, $WorkingDirectory, $TimeoutMilliseconds, $OnStarted)
    if ($null -ne $OnStarted) {
        if ($flowHarness.ProcessStartFailure) {
            $flowHarness.Injected = $true
            $flowHarness.ExpectedOperation = $script:E2eDiagnostics.operation_id
            $flowHarness.ExpectedLastCompleted = $script:E2eDiagnostics.last_completed_phase
            return [pscustomobject]@{ started = $false; completed = $false; exit_code = $null; error_category = 'process_start' }
        }
        & $OnStarted
    }
    Invoke-FlowBoundary
    if ($script:E2eDiagnostics.operation_id -eq 'postgres_schema_reset') { $flowHarness.SchemaTransportReached = $true }
    if ($script:E2eDiagnostics.operation_id -eq 'administrator_bootstrap') { $flowHarness.BootstrapTransportReached = $true }
    return [pscustomobject]@{ started = $true; completed = $true; exit_code = 0; error_category = $null }
}
function Invoke-E2ePrivateBootstrap {
    param($FilePath, $Arguments, $StandardInput, $WorkingDirectory, $TimeoutMilliseconds, $OnStarted)
    Invoke-E2ePrivateProcess @PSBoundParameters
}

$expectedPhases = @(
    'safety_preflight', 'docker_context_validation', 'compose_config_validation',
    'database_start', 'database_readiness', 'database_runtime_validation',
    'database_schema_reset', 'browser_installation', 'image_build', 'application_start',
    'application_readiness', 'administrator_bootstrap', 'fixture_seed', 'playwright_start',
    'playwright_execution'
)
$scenarios = @($expectedPhases + @('cleanup', 'success', 'schema_transport', 'playwright_process_start'))
$passed = 0
foreach ($scenario in $scenarios) {
    $flowHarness = [pscustomobject]@{
        RunnerPath = $runnerPath; FailPhase = $scenario; FailOperation = $null; Injected = $false
        ResolvedApplication = 'mock-executable'
        ExpectedOperation = $null; ExpectedLastCompleted = $null
        ExpectedCategory = 'nonzero_exit'; ExpectedExit = 17; ProcessStartFailure = $false
        Phases = [Collections.Generic.List[string]]::new()
        Completed = [Collections.Generic.List[string]]::new()
        Boundaries = [Collections.Generic.List[string]]::new()
        Removed = [Collections.Generic.HashSet[string]]::new()
        Diagnostics = [Collections.Generic.List[string]]::new()
        Port = 21000; CredentialCleanupCount = 0; DirectoryCleanupCount = 0; ManagedDirectoryCount = 0
        MutexCleaned = $false; MutexAcquired = $false
        SchemaTransportReached = $false; BootstrapTransportReached = $false
        Secret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    }
    if ($scenario -eq 'schema_transport') {
        $flowHarness.FailPhase = 'database_schema_reset'
        $flowHarness.FailOperation = 'postgres_schema_reset'
    }
    if ($scenario -eq 'playwright_process_start') {
        $flowHarness.FailPhase = 'playwright_start'
        $flowHarness.ProcessStartFailure = $true
        $flowHarness.ExpectedCategory = 'process_start'
        $flowHarness.ExpectedExit = $null
    }
    $outcome = [pscustomobject]@{ Failure = $null }
    $output = @(& {
        try { & $mainBody }
        catch { $outcome.Failure = $_.Exception.Message }
    } 2>&1 3>&1 4>&1 5>&1 6>&1)
    $allText = ($output | Out-String) + $outcome.Failure + ($flowHarness.Diagnostics -join [Environment]::NewLine)
    Assert-FlowRegression (-not $allText.Contains($flowHarness.Secret))
    Assert-FlowRegression ($flowHarness.CredentialCleanupCount -ge 2)
    if ($scenario -eq 'success') {
        Assert-FlowRegression ($null -eq $outcome.Failure -and -not $flowHarness.Injected)
        Assert-FlowRegression (($flowHarness.Phases -join ',') -ceq ($expectedPhases -join ','))
        Assert-FlowRegression (($flowHarness.Completed -join ',') -ceq (($expectedPhases + 'cleanup') -join ','))
        Assert-FlowRegression ($flowHarness.SchemaTransportReached -and $flowHarness.BootstrapTransportReached)
        Assert-FlowRegression ($flowHarness.MutexCleaned -and $flowHarness.DirectoryCleanupCount -eq 2)
        Assert-FlowRegression ($script:E2eDiagnostics.log_check_status -eq 'passed')
    }
    else {
        Assert-FlowRegression $flowHarness.Injected
        Assert-FlowRegression ($output.Count -eq 0)
        $failedIndex = [Array]::IndexOf($expectedPhases, $flowHarness.FailPhase)
        $expectedStarted = if ($failedIndex -ge 0) { @($expectedPhases[0..$failedIndex]) } else { $expectedPhases }
        Assert-FlowRegression (($flowHarness.Phases -join ',') -ceq ($expectedStarted -join ','))
        $expectedCompleted = @(if ($failedIndex -gt 0) { $expectedPhases[0..($failedIndex - 1)] }
            elseif ($failedIndex -eq 0) { } else { $expectedPhases })
        if ($scenario -ne 'cleanup') { $expectedCompleted += 'cleanup' }
        Assert-FlowRegression (($flowHarness.Completed -join ',') -ceq ($expectedCompleted -join ','))
        $snapshot = $script:E2eDiagnostics.failure_snapshot
        Assert-FlowRegression ($snapshot.current_phase -ceq $flowHarness.FailPhase -and
            $snapshot.operation_id -ceq $flowHarness.ExpectedOperation -and
            $snapshot.last_completed_phase -ceq $flowHarness.ExpectedLastCompleted -and
            $snapshot.error_category -ceq $flowHarness.ExpectedCategory -and $snapshot.exit_code -eq $flowHarness.ExpectedExit)
        $safeExit = if ($null -eq $flowHarness.ExpectedExit) { 'null' } else { [string]$flowHarness.ExpectedExit }
        Assert-FlowRegression ($outcome.Failure -match '(?m)^schema_version=1\r?$' -and
            $outcome.Failure -match ('(?m)^exit_code=' + $safeExit + '\r?$') -and
            $outcome.Failure -match ('(?m)^error_category=' + $flowHarness.ExpectedCategory + '\r?$'))
        if ($flowHarness.Diagnostics.Count -gt 0) {
            Assert-FlowRegression ($flowHarness.Diagnostics.Count -eq 1 -and $flowHarness.Diagnostics[0] -ceq $outcome.Failure)
        }
        if ($flowHarness.MutexAcquired) { Assert-FlowRegression $flowHarness.MutexCleaned }
        if ($flowHarness.ManagedDirectoryCount -gt 0) { Assert-FlowRegression ($flowHarness.DirectoryCleanupCount -ge 1) }
        else { Assert-FlowRegression ($flowHarness.DirectoryCleanupCount -eq 0) }
        $expectedLog = if ($scenario -eq 'cleanup') { 'passed' } else { 'not_run' }
        Assert-FlowRegression ($script:E2eDiagnostics.log_check_status -ceq $expectedLog)
    }
    $passed++
    Write-Host ('PASS: actual runner flow; scenario={0}' -f $scenario)
}

# Exercise the actual read wrapper, not the flow double: a failed native launch
# must not inherit a previous successful command's exit code on Windows PS 5.1.
foreach ($readCase in @('missing_application', 'actual_node_query')) {
    $script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
    Start-E2eDiagnosticOperation $script:E2eDiagnostics 'safety_preflight' 'node_version_check'
    $flowHarness.ResolvedApplication = if ($readCase -eq 'missing_application') {
        Join-Path $PSScriptRoot ('missing-application-' + $flowHarness.Secret + '.exe')
    }
    else { $actualNodeApplication }
    $global:LASTEXITCODE = 0
    $LASTEXITCODE = 0
    $readOutcome = [pscustomobject]@{ Failure = $null }
    $captured = @(& {
        try { & $realReadCommand -Executable 'node' -Arguments @('--version') -OperationId 'node_version_check' }
        catch { $readOutcome.Failure = $_.Exception.Message }
    } 2>&1 3>&1 4>&1 5>&1 6>&1)
    $safeText = ($captured | Out-String) + $readOutcome.Failure +
        (ConvertTo-E2eSafeDiagnostics $script:E2eDiagnostics 'E2E_PREREQUISITE_FAILED')
    Assert-FlowRegression (-not $safeText.Contains($flowHarness.Secret))
    if ($readCase -eq 'missing_application') {
        Assert-FlowRegression ($captured.Count -eq 0 -and $readOutcome.Failure -ceq 'E2E_SAFE_OPERATION_FAILED')
        $snapshot = $script:E2eDiagnostics.failure_snapshot
        Assert-FlowRegression ($snapshot.current_phase -ceq 'safety_preflight' -and
            $snapshot.operation_id -ceq 'node_version_check' -and
            $snapshot.error_category -ceq 'process_start' -and $null -eq $snapshot.exit_code)
    }
    else {
        Assert-FlowRegression ($null -eq $readOutcome.Failure -and $null -eq $script:E2eDiagnostics.failure_snapshot)
        Assert-FlowRegression ($captured.Count -eq 1 -and [string]$captured[0] -match '^v[0-9]+\.[0-9]+\.[0-9]+$')
    }
    $passed++
    Write-Host ('PASS: actual read wrapper; scenario={0}' -f $readCase)
}
Write-Host ('E2E runner flow tests: {0} passed, 0 skipped, 0 failed.' -f $passed)
