$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')
. (Join-Path $PSScriptRoot 'e2e-private-process.ps1')
. (Join-Path $PSScriptRoot 'e2e-phase-diagnostics.ps1')
. (Join-Path $PSScriptRoot 'e2e-runner-operations.ps1')

function Assert-Transport {
    param([bool] $Condition, [string] $Name)
    if (-not $Condition) {
        $script:TransportFailedAssertion = $Name
        throw ('E2E_STDIN_REGRESSION_FAILED: ' + $Name)
    }
}

function Get-TransportDigest {
    param([byte[]] $Bytes)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Invoke-TransportCaptured {
    param(
        [string] $FilePath,
        [string[]] $Arguments,
        [AllowEmptyString()] [string] $InputText,
        [int] $TimeoutMilliseconds = 10000,
        [scriptblock] $OnStarted
    )
    # Merge all six PS streams before examining them. Never print a captured item
    # or exception: a deliberately hostile child echoes the synthetic secret.
    $captured = @(& {
        Invoke-E2ePrivateProcess -FilePath $FilePath -Arguments $Arguments `
            -StandardInput $InputText -TimeoutMilliseconds $TimeoutMilliseconds `
            -WorkingDirectory $script:TransportScratch -OnStarted $OnStarted
    } *>&1)
    Assert-Transport ($captured.Count -eq 1) 'result_stream_is_private'
    $result = $captured[0]
    $propertyNames = @($result.PSObject.Properties.Name | Sort-Object)
    Assert-Transport (($propertyNames -join ',') -eq 'completed,error_category,exit_code,started') 'result_has_safe_fields_only'
    $serialized = ConvertTo-Json -InputObject $result -Compress -Depth 4
    Assert-Transport (-not $serialized.Contains($script:TransportSecret)) 'result_contains_no_secret'
    return $result
}

function Assert-TransportResult {
    param($Result, [bool] $Started, [bool] $Completed, $ExitCode, $Category, [string] $Name)
    Assert-Transport ($Result.started -is [bool] -and $Result.started -eq $Started) ($Name + '_started')
    Assert-Transport ($Result.completed -is [bool] -and $Result.completed -eq $Completed) ($Name + '_completed')
    Assert-Transport ($Result.exit_code -eq $ExitCode) ($Name + '_exit_code')
    Assert-Transport ($Result.error_category -eq $Category) ($Name + '_category')
    $script:TransportPassed += 1
    Write-Host ('PASS ' + $Name)
}

function Invoke-TransportByteCase {
    param([AllowEmptyString()] [string] $InputText, [string] $Name, [switch] $Nonzero)
    $bytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($InputText)
    try {
        $digest = Get-TransportDigest -Bytes $bytes
        $lineCount = @($bytes | Where-Object { $_ -eq 10 }).Count
        $mode = if ($Nonzero) { 'nonzero' } else { 'bytes' }
        $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments @(
            $script:TransportChild, $mode, $digest, [string]$bytes.Length, [string]$lineCount
        ) -InputText $InputText
        if ($Nonzero) { Assert-TransportResult $result $true $true 23 'nonzero_exit' $Name }
        else { Assert-TransportResult $result $true $true 0 $null $Name }
    }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Test-RealSchemaResetTransport {
    # Execute only the actual function definitions, never the E2E runner body.
    # The SQL stays in memory; assertions emit only fixed test identifiers.
    $runnerPath = Join-Path $PSScriptRoot 'test-demo-e2e.ps1'
    $parseTokens = $parseErrors = $null
    $runnerAst = [Management.Automation.Language.Parser]::ParseFile($runnerPath, [ref]$parseTokens, [ref]$parseErrors)
    Assert-Transport ($parseErrors.Count -eq 0) 'reset_runner_parses'
    $definitions = @{}
    foreach ($name in @('Reset-E2eDatabaseSchema', 'Invoke-E2eComposeWithStandardInput')) {
        $found = @($runnerAst.FindAll({
            param($ast)
            $ast -is [Management.Automation.Language.FunctionDefinitionAst] -and $ast.Name -eq $name
        }, $true))
        Assert-Transport ($found.Count -eq 1) 'reset_actual_definition_is_unique'
        $definitions[$name] = $found[0]
        . ([scriptblock]::Create($found[0].Extent.Text))
    }
    $statementAssignments = @($definitions['Reset-E2eDatabaseSchema'].FindAll({
        param($ast)
        $ast -is [Management.Automation.Language.AssignmentStatementAst] -and
            $ast.Left -is [Management.Automation.Language.VariableExpressionAst] -and
            $ast.Left.VariablePath.UserPath -eq 'statement'
    }, $true))
    Assert-Transport ($statementAssignments.Count -eq 1) 'reset_sql_input_exists'

    $script:E2eProjectName = 'reawote-e2e'
    $script:BaseComposePath = Join-Path $repositoryRoot 'docker-compose.yml'
    $script:E2eComposePath = Join-Path $repositoryRoot 'docker-compose.e2e.yml'
    $script:E2eDatabaseUser = 'reawote_e2e'
    $script:E2eDatabaseName = 'reawote_e2e'
    $script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
    Start-E2eRunnerPhase -Phase 'database_schema_reset' -OperationId 'postgres_schema_reset'
    $events = [Collections.Generic.List[string]]::new()
    $denyVolume = $false
    $callsiteSecret = $script:TransportSecret + "'" + [char]0x017E
    $passwordLiteral = $callsiteSecret.Replace("'", "''")
    # Evaluate the production SQL expression separately from either wrapper, then
    # verify its complete value at the final private-process boundary and child.
    $expectedInput = & ([scriptblock]::Create($statementAssignments[0].Right.Extent.Text))
    Assert-Transport ($expectedInput -is [string] -and $expectedInput.Contains($passwordLiteral)) 'reset_sql_contains_private_escaped_input'
    $expectedBytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($expectedInput)
    $expectedDigest = Get-TransportDigest -Bytes $expectedBytes
    $expectedLines = @($expectedBytes | Where-Object { $_ -eq 10 }).Count
    $realPrivateTransport = (Get-Command Invoke-E2ePrivateProcess -CommandType Function).ScriptBlock

    function Assert-E2eResourcesOwned { $events.Add('ownership') }
    function Assert-RuntimeDatabaseMount { $events.Add('runtime_volume') }
    function Assert-E2eDatabaseEngineVolume {
        param([switch] $RequirePresent)
        Assert-Transport $RequirePresent.IsPresent 'reset_volume_must_exist'
        $events.Add('engine_volume')
        if ($denyVolume) { throw 'E2E_STDIN_REGRESSION_GUARD_REFUSED' }
    }
    function Assert-LocalDockerContext { $events.Add('local_context') }
    function Get-Command {
        param([string] $Name, $CommandType, $ErrorAction)
        if ($Name -eq 'docker') { return [pscustomobject]@{ Source = 'private-test-docker' } }
        return Microsoft.PowerShell.Core\Get-Command -Name $Name -CommandType $CommandType -ErrorAction Stop
    }
    function Invoke-E2ePrivateProcess {
        param([string] $FilePath, [string[]] $Arguments, [string] $StandardInput, [string] $WorkingDirectory, [int] $TimeoutMilliseconds)
        $events.Add('private_transport')
        Assert-Transport ($FilePath -eq 'private-test-docker') 'reset_resolves_docker_dispatch'
        Assert-Transport ($Arguments[0] -eq 'compose' -and $Arguments -contains 'psql' -and $Arguments -contains 'ON_ERROR_STOP=1') 'reset_dispatches_compose_psql'
        Assert-Transport (($Arguments -join "`n").IndexOf($callsiteSecret, [StringComparison]::Ordinal) -lt 0) 'reset_secret_not_in_arguments'
        Assert-Transport ($StandardInput -ceq $expectedInput) 'reset_preserves_whole_sql_input'
        Assert-Transport ($script:E2eDiagnostics.operation_id -eq 'postgres_schema_reset') 'reset_operation_precedes_process'
        return & $realPrivateTransport -FilePath $script:TransportNode -Arguments @(
            $script:TransportChild, 'bytes', $expectedDigest, [string]$expectedBytes.Length, [string]$expectedLines
        ) -StandardInput $StandardInput -WorkingDirectory $script:TransportScratch -TimeoutMilliseconds 10000
    }
    try {
        $captured = @(& { Reset-E2eDatabaseSchema -Password $callsiteSecret } *>&1)
        Assert-Transport ($captured.Count -eq 0) 'reset_never_returns_private_output'
        Assert-Transport (($events -join ',') -eq 'ownership,runtime_volume,engine_volume,local_context,private_transport') 'reset_safety_precedes_real_transport'
        $script:TransportPassed += 1
        Write-Host 'PASS actual_schema_reset_callsite_uses_exact_private_transport'

        $events.Clear()
        $denyVolume = $true
        $refused = $false
        try { Reset-E2eDatabaseSchema -Password $callsiteSecret }
        catch { $refused = $true }
        Assert-Transport $refused 'reset_rejects_unsafe_volume'
        Assert-Transport (($events -join ',') -eq 'ownership,runtime_volume,engine_volume') 'reset_rejection_never_starts_process'
        $script:TransportPassed += 1
        Write-Host 'PASS actual_schema_reset_guard_blocks_transport'
    }
    finally {
        [Array]::Clear($expectedBytes, 0, $expectedBytes.Length)
        $expectedInput = $passwordLiteral = $callsiteSecret = $null
    }
}

$script:TransportPassed = 0
$script:TransportFailedAssertion = 'unknown_safe_failure'
$repositoryRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$scratchParent = [IO.Path]::GetFullPath((Join-Path $repositoryRoot 'tmp'))
$script:TransportScratch = [IO.Path]::GetFullPath((Join-Path $scratchParent ('e2e-stdin-' + [guid]::NewGuid().ToString('N'))))
$script:TransportNode = (Get-Command node -CommandType Application -ErrorAction Stop).Source
$script:TransportChild = Join-Path $PSScriptRoot 'e2e-stdin-transport-child.mjs'
$script:TransportSecret = 'synthetic-' + [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')

try {
    Assert-Transport ($script:TransportScratch.StartsWith($scratchParent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) 'scratch_is_scoped'
    [void][IO.Directory]::CreateDirectory($script:TransportScratch)
    $unicode = [string][char]0x017E + [char]0x006C + [char]0x0075 + [char]0x0165 + [char]0x006F + [char]0x0075 + [char]0x010D + [char]0x006B + [char]0x00FD

    Invoke-TransportByteCase -InputText '' -Name 'empty_stdin_eof'
    Invoke-TransportByteCase -InputText ($unicode + $script:TransportSecret) -Name 'utf8_without_bom_or_added_newline'
    Invoke-TransportByteCase -InputText ($script:TransportSecret + "`n" + $script:TransportSecret + "`n") -Name 'two_separate_password_lines'
    Invoke-TransportByteCase -InputText ("SELECT 1;`nBEGIN;`nSELECT '" + $unicode + "';`nROLLBACK;`n") -Name 'multiline_sql_exact_lf'
    Invoke-TransportByteCase -InputText ($script:TransportSecret + "`r`nend`n") -Name 'mixed_newlines_preserved'
    Invoke-TransportByteCase -InputText (($script:TransportSecret + "`n") * 128) -Name 'stdout_stderr_echo_is_discarded'
    Invoke-TransportByteCase -InputText ($script:TransportSecret + "`n") -Name 'nonzero_exit_is_structured' -Nonzero

    $arguments = @($script:TransportChild, 'arguments', '', 'two words', 'double"quote', "single'quote", 'backslash\', 'quote\"end', $unicode, '$(exit 99)', '& exit 99')
    $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments $arguments -InputText ''
    Assert-TransportResult $result $true $true 0 $null 'argument_array_has_no_shell_interpolation'

    $result = Invoke-TransportCaptured -FilePath (Join-Path $script:TransportScratch 'missing-executable.exe') -Arguments @('safe-argument') -InputText $script:TransportSecret
    Assert-TransportResult $result $false $false $null 'process_start' 'process_start_failure_is_structured'

    $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments @($script:TransportChild, 'closed-stdin') -InputText ($script:TransportSecret * 65536)
    Assert-TransportResult $result $true $false $null 'stdin_io' 'closed_stdin_is_structured'

    $markerPath = Join-Path $script:TransportScratch 'descendant-survived.txt'
    $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments @($script:TransportChild, 'timeout-tree', $markerPath) -InputText '' -TimeoutMilliseconds 600
    Assert-TransportResult $result $true $false $null 'timeout' 'timeout_is_structured'
    Start-Sleep -Milliseconds 2200
    Assert-Transport (-not (Test-Path -LiteralPath $markerPath)) 'timeout_kills_only_child_tree'
    $script:TransportPassed += 1
    Write-Host 'PASS timeout_terminates_descendant'

    $script:TransportCallbackEvents = [Collections.Generic.List[string]]::new()
    $script:TransportCallbackGate = Join-Path $script:TransportScratch 'callback-gate.txt'
    $releaseGate = {
        $script:TransportCallbackEvents.Add('started')
        [IO.File]::WriteAllText($script:TransportCallbackGate, 'ready', [Text.UTF8Encoding]::new($false))
    }
    $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments @(
        $script:TransportChild, 'callback-gate', $script:TransportCallbackGate, '0'
    ) -InputText '' -OnStarted $releaseGate
    $script:TransportCallbackEvents.Add('completed')
    Assert-Transport (($script:TransportCallbackEvents -join ',') -eq 'started,completed') 'started_callback_once_before_child_completion'
    Assert-TransportResult $result $true $true 0 $null 'started_callback_releases_actual_child'
    Remove-Item -LiteralPath $script:TransportCallbackGate -Force

    $script:TransportCallbackEvents.Clear()
    $result = Invoke-TransportCaptured -FilePath (Join-Path $script:TransportScratch 'missing-callback-child.exe') `
        -Arguments @('safe-argument') -InputText '' -OnStarted $releaseGate
    Assert-Transport ($script:TransportCallbackEvents.Count -eq 0) 'started_callback_not_called_when_spawn_fails'
    Assert-Transport (-not (Test-Path -LiteralPath $script:TransportCallbackGate)) 'failed_spawn_never_releases_gate'
    Assert-TransportResult $result $false $false $null 'process_start' 'missing_executable_never_calls_started_callback'

    $script:TransportCallbackEvents.Clear()
    $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments @(
        $script:TransportChild, 'callback-gate', $script:TransportCallbackGate, '23'
    ) -InputText '' -OnStarted $releaseGate
    Assert-Transport (($script:TransportCallbackEvents -join ',') -eq 'started') 'nonzero_child_started_callback_once'
    Assert-TransportResult $result $true $true 23 'nonzero_exit' 'nonzero_child_still_calls_started_callback'
    Remove-Item -LiteralPath $script:TransportCallbackGate -Force

    $result = Invoke-TransportCaptured -FilePath $script:TransportNode -Arguments @(
        $script:TransportChild, 'callback-gate', $script:TransportCallbackGate, '0'
    ) -InputText '' -OnStarted {
        Write-Output $script:TransportSecret
        Write-Warning $script:TransportSecret
        Write-Host $script:TransportSecret
    }
    Assert-TransportResult $result $true $false $null 'unknown_safe_failure' 'callback_secret_output_is_suppressed_and_rejected'

    Test-RealSchemaResetTransport

    Assert-Transport (@(Get-ChildItem -LiteralPath $script:TransportScratch -Force).Count -eq 0) 'no_secret_artifacts_created'
    $script:TransportPassed += 1
    Write-Host 'PASS no_secret_artifacts_created'
}
catch {
    # Never expose original error records, child output or request inputs.
    Write-Host ('E2E_STDIN_TRANSPORT_RESULTS passed=' + $script:TransportPassed + ' failed=1 skipped=0 assertion=' + $script:TransportFailedAssertion)
    throw 'E2E_STDIN_REGRESSION_FAILED: A private transport assertion failed.'
}
finally {
    $script:TransportSecret = $null
    if (Test-Path -LiteralPath $script:TransportScratch) {
        $resolvedScratch = [IO.Path]::GetFullPath($script:TransportScratch)
        if (-not $resolvedScratch.StartsWith($scratchParent + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'E2E_STDIN_REGRESSION_FAILED: Scratch cleanup scope rejected.'
        }
        Assert-E2eTreeNoReparse $resolvedScratch
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    }
}
Write-Host ('E2E_STDIN_TRANSPORT_RESULTS passed=' + $script:TransportPassed + ' failed=0 skipped=0')
