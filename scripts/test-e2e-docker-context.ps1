$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

# Only executable resolution and the OS process boundary are doubled. Every
# case uses the real Assert-LocalDockerContext -> Invoke-E2eReadCommand path.
$realResolver = (Get-Command Resolve-E2eReadExecutable).ScriptBlock
$script:contextSecret = 'synthetic-' + [Guid]::NewGuid().ToString('N')
$script:localEndpoint = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    'npipe:////./pipe/dockerDesktopLinuxEngine'
} else { 'unix:///var/run/docker.sock' }
$originalDockerHost = [Environment]::GetEnvironmentVariable('DOCKER_HOST', 'Process')
$script:passed = 0

function Assert-ContextTest {
    param([bool] $Condition, [string] $Label)
    if (-not $Condition) { throw ('Docker context regression failed: ' + $Label) }
    $script:passed++
}

function Resolve-E2eReadExecutable {
    param([string] $Executable, [string] $OperationId)
    Set-E2eRunnerOperation -OperationId $OperationId
    $script:contextTrace.Add($OperationId)
    if ($script:contextCase.Name -eq 'executable_missing') {
        # Real application-only resolution of an intentionally nonexistent name.
        return & $realResolver -Executable ('missing-' + $script:contextSecret + '.exe') -OperationId $OperationId
    }
    return (Join-Path $PSScriptRoot 'unused-docker-test-double.exe')
}

function Invoke-E2eNativeReadProcess {
    param([string] $FilePath, [string[]] $Arguments)
    $operation = $script:E2eDiagnostics.operation_id
    $script:contextTrace.Add($operation)
    $expectedArguments = switch ($operation) {
        docker_context_show { @('context', 'show') }
        docker_context_inspect { @('context', 'inspect', 'desktop-linux') }
        docker_context_policy_validation { @('--context', 'desktop-linux', 'info', '--format', '{{.OSType}}') }
        default { throw 'Unexpected process operation in read-only test.' }
    }
    Assert-ContextTest (($Arguments -join '|') -ceq ($expectedArguments -join '|')) 'only fixed read-only commands are invoked'
    $lines = switch ($operation) {
        docker_context_show { 'desktop-linux' }
        docker_context_inspect {
            # Sensitive extra fields are never copied into diagnostics.
            ConvertTo-Json -Depth 5 -InputObject @(@{
                Name = 'desktop-linux'; Endpoints = @{ docker = @{ Host = $script:localEndpoint } }
                secret = $script:contextSecret
            })
        }
        docker_context_policy_validation { 'linux' }
    }
    $result = [pscustomobject]@{
        started = $true; exit_code = 0; system_error_code = $null
        stdout = @($lines); error_category = $null; stderr = $script:contextSecret
    }
    if ($operation -eq $script:contextCase.Target) {
        foreach ($key in $script:contextCase.Override.Keys) { $result.$key = $script:contextCase.Override[$key] }
    }
    return $result
}

$show = 'docker_context_show'
$inspect = 'docker_context_inspect'
$parse = 'docker_context_parse'
$policy = 'docker_context_policy_validation'
$cases = @(
    @{ Name = 'executable_missing'; Operation = 'docker_executable_resolution'; Category = 'executable_not_found'; Started = $false; Exit = $null; Calls = 0 },
    @{ Name = 'process_start'; Target = $show; Override = @{ started = $false; exit_code = $null; error_category = 'process_start'; system_error_code = 2; stdout = @($script:contextSecret) }; Operation = $show; Category = 'process_start'; Started = $false; Exit = $null; Calls = 1; SystemCode = 2 },
    @{ Name = 'show_nonzero'; Target = $show; Override = @{ exit_code = 17; stdout = @($script:contextSecret) }; Operation = $show; Category = 'nonzero_exit'; Started = $true; Exit = 17; Calls = 1 },
    @{ Name = 'show_missing_exit'; Target = $show; Override = @{ exit_code = $null }; Operation = $show; Category = 'missing_exit_code'; Started = $true; Exit = $null; Calls = 1 },
    @{ Name = 'show_string_exit'; Target = $show; Override = @{ exit_code = '0' }; Operation = $show; Category = 'missing_exit_code'; Started = $true; Exit = $null; Calls = 1 },
    @{ Name = 'show_empty'; Target = $show; Override = @{ stdout = @() }; Operation = $show; Category = 'empty_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'show_whitespace'; Target = $show; Override = @{ stdout = @('   ') }; Operation = $show; Category = 'empty_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'show_multiline'; Target = $show; Override = @{ stdout = @('desktop-', 'linux') }; Operation = $show; Category = 'invalid_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'show_embedded_newline'; Target = $show; Override = @{ stdout = @("desktop-linux`n$script:contextSecret") }; Operation = $show; Category = 'invalid_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'show_trailing_newline'; Target = $show; Override = @{ stdout = @("desktop-linux`n") }; Operation = $show; Category = 'invalid_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'show_option'; Target = $show; Override = @{ stdout = @('--help') }; Operation = $show; Category = 'invalid_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'show_object'; Target = $show; Override = @{ stdout = @(@{ secret = $script:contextSecret }) }; Operation = $show; Category = 'invalid_output'; Started = $true; Exit = 0; Calls = 1 },
    @{ Name = 'inspect_nonzero'; Target = $inspect; Override = @{ exit_code = 23; stdout = @($script:contextSecret) }; Operation = $inspect; Category = 'nonzero_exit'; Started = $true; Exit = 23; Calls = 2 },
    @{ Name = 'inspect_missing_exit'; Target = $inspect; Override = @{ exit_code = $null }; Operation = $inspect; Category = 'missing_exit_code'; Started = $true; Exit = $null; Calls = 2 },
    @{ Name = 'inspect_empty'; Target = $inspect; Override = @{ stdout = @() }; Operation = $inspect; Category = 'empty_output'; Started = $true; Exit = 0; Calls = 2 },
    @{ Name = 'invalid_json'; Target = $inspect; Override = @{ stdout = @('[' + $script:contextSecret) }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_object'; Target = $inspect; Override = @{ stdout = @('{}') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_empty_array'; Target = $inspect; Override = @{ stdout = @('[]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_multiple'; Target = $inspect; Override = @{ stdout = @('[{},{}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_missing_fields'; Target = $inspect; Override = @{ stdout = @('[{}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_wrong_host_type'; Target = $inspect; Override = @{ stdout = @('[{"Name":"desktop-linux","Endpoints":{"docker":{"Host":42}}}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_wrong_name'; Target = $inspect; Override = @{ stdout = @('[{"Name":"other","Endpoints":{"docker":{"Host":"tcp://remote"}}}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_name_array'; Target = $inspect; Override = @{ stdout = @('[{"Name":["desktop-linux"],"Endpoints":{"docker":{"Host":"' + $script:localEndpoint + '"}}}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_endpoints_array'; Target = $inspect; Override = @{ stdout = @('[{"Name":"desktop-linux","Endpoints":[{"docker":{"Host":"' + $script:localEndpoint + '"}}]}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_docker_array'; Target = $inspect; Override = @{ stdout = @('[{"Name":"desktop-linux","Endpoints":{"docker":[{"Host":"' + $script:localEndpoint + '"}]}}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'json_host_array'; Target = $inspect; Override = @{ stdout = @('[{"Name":"desktop-linux","Endpoints":{"docker":{"Host":["' + $script:localEndpoint + '"]}}}]') }; Operation = $parse; Category = 'invalid_output'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'remote_context'; Target = $inspect; Override = @{ stdout = @('[{"Name":"desktop-linux","Endpoints":{"docker":{"Host":"ssh://' + $script:contextSecret + '"}}}]') }; Operation = $policy; Category = 'policy_rejected'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'remote_docker_host'; HostValue = ('tcp://' + $script:contextSecret); Operation = $policy; Category = 'policy_rejected'; Started = $false; Exit = $null; Calls = 0 },
    @{ Name = 'local_host_newline'; HostValue = ($script:localEndpoint + "`n"); Operation = $policy; Category = 'policy_rejected'; Started = $false; Exit = $null; Calls = 0 },
    @{ Name = 'mismatched_local_host'; HostValue = ($script:localEndpoint + '-other'); Operation = $policy; Category = 'policy_rejected'; Started = $null; Exit = $null; Calls = 2 },
    @{ Name = 'engine_nonzero'; Target = $policy; Override = @{ exit_code = 1 }; Operation = $policy; Category = 'nonzero_exit'; Started = $true; Exit = 1; Calls = 3 },
    @{ Name = 'engine_empty'; Target = $policy; Override = @{ stdout = @() }; Operation = $policy; Category = 'empty_output'; Started = $true; Exit = 0; Calls = 3 },
    @{ Name = 'engine_invalid'; Target = $policy; Override = @{ stdout = @($script:contextSecret) }; Operation = $policy; Category = 'invalid_output'; Started = $true; Exit = 0; Calls = 3 },
    @{ Name = 'windows_engine'; Target = $policy; Override = @{ stdout = @('windows') }; Operation = $policy; Category = 'policy_rejected'; Started = $true; Exit = 0; Calls = 3 },
    @{ Name = 'local_success'; Success = $true; Calls = 3 },
    @{ Name = 'local_matching_host_success'; HostValue = $script:localEndpoint; Success = $true; Calls = 3 }
)
$allowedKeys = @('schema_version', 'error_code', 'last_completed_phase', 'current_phase', 'failed_phase',
    'operation_id', 'error_category', 'process_started', 'exit_code', 'system_error_code', 'log_check_status')
try {
    foreach ($case in $cases) {
        $script:contextCase = @{ Target = ''; Override = @{}; Success = $false; HostValue = $null; SystemCode = $null }
        foreach ($key in $case.Keys) { $script:contextCase[$key] = $case[$key] }
        [Environment]::SetEnvironmentVariable('DOCKER_HOST', $script:contextCase.HostValue, 'Process')
        $script:contextTrace = [Collections.Generic.List[string]]::new()
        $script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
        Start-E2eRunnerPhase -Phase 'safety_preflight' -OperationId 'repository_validation'
        Complete-E2eRunnerPhase
        Start-E2eRunnerPhase -Phase 'docker_context_validation' -OperationId 'docker_executable_resolution'
        $captured = [Collections.Generic.List[object]]::new()
        $safeError = $null
        try { Assert-LocalDockerContext 2>&1 3>&1 4>&1 5>&1 6>&1 | ForEach-Object { $captured.Add($_) } }
        catch { $safeError = $_.Exception.Message }
        $diagnostic = ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode 'E2E_ISOLATION_FAILED'
        $publicText = (($captured | ForEach-Object { [string]$_ }) -join "`n") + $safeError + $diagnostic
        foreach ($forbidden in @($script:contextSecret, $script:localEndpoint, 'DOCKER_HOST', 'PATH=', 'stdout=', 'stderr=', 'Exception', 'credentials=', 'cookie=', 'csrf=')) {
            Assert-ContextTest (-not $publicText.Contains($forbidden)) ($case.Name + ' excludes private data')
        }
        $keys = @(($diagnostic -split '\r?\n') | ForEach-Object { ($_ -split '=', 2)[0] })
        Assert-ContextTest (($keys -join ',') -ceq ($allowedKeys -join ',')) ($case.Name + ' exact diagnostic allowlist')
        $processCalls = @($script:contextTrace | Where-Object { $_ -ne 'docker_executable_resolution' }).Count
        Assert-ContextTest ($processCalls -eq $case.Calls) ($case.Name + ' fails closed before further commands')
        if ($script:contextCase.Success) {
            Assert-ContextTest ($null -eq $safeError) ($case.Name + ' successful validation')
            Assert-ContextTest ($captured.Count -eq 1 -and $captured[0] -ceq 'desktop-linux') ($case.Name + ' only internal context name returned')
            Assert-ContextTest (($script:contextTrace -join ',') -ceq 'docker_executable_resolution,docker_context_show,docker_context_inspect,docker_context_policy_validation') ($case.Name + ' operation sequence')
            Complete-E2eRunnerPhase
            Assert-ContextTest ($script:E2eDiagnostics.last_completed_phase -eq 'docker_context_validation') ($case.Name + ' phase complete')
        }
        else {
            Assert-ContextTest ($safeError -ceq 'E2E_SAFE_OPERATION_FAILED') ($case.Name + ' safe exception only')
            Assert-ContextTest ($captured.Count -eq 0) ($case.Name + ' no output on failure')
            $failure = $script:E2eDiagnostics.failure_snapshot
            Assert-ContextTest ($null -ne $failure) ($case.Name + ' failure captured')
            Assert-ContextTest ($failure.operation_id -ceq $case.Operation) ($case.Name + ' precise operation: ' + $failure.operation_id)
            Assert-ContextTest ($failure.error_category -ceq $case.Category) ($case.Name + ' precise category')
            Assert-ContextTest ($failure.process_started -ceq $case.Started) ($case.Name + ' process started')
            Assert-ContextTest ($failure.exit_code -ceq $case.Exit) ($case.Name + ' numeric exit')
            Assert-ContextTest ($failure.system_error_code -ceq $script:contextCase.SystemCode) ($case.Name + ' numeric system error')
            Assert-ContextTest ($failure.current_phase -eq 'docker_context_validation' -and $failure.last_completed_phase -eq 'safety_preflight') ($case.Name + ' phase history')
        }
    }
}
finally { [Environment]::SetEnvironmentVariable('DOCKER_HOST', $originalDockerHost, 'Process') }
Write-Host "Docker context boundary regressions: $script:passed passed, 0 skipped, 0 failed ($($cases.Count) cases; mocked OS boundary, except real missing-executable resolution)."
