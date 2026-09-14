$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

# Load the actual runner functions, never its main body. Only Docker process
# boundaries below are doubles; parsing, ownership and cleanup flow are real.
$tokens = $parseErrors = $null
$runnerAst = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot 'test-demo-e2e.ps1'), [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw 'E2E_RUNTIME_CLEANUP_SOURCE_INVALID' }
foreach ($statement in $runnerAst.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:E2eProjectName = 'reawote-e2e'
$script:E2eDatabaseVolumeName = 'reawote-e2e-postgres-data'
$script:CleanupSecret = 'synthetic-cleanup-secret-' + [guid]::NewGuid().ToString('N')
$script:CleanupCase = ''
$script:CleanupChecks = 0
$script:CleanupFixture = $null
$script:CleanupMutations = $null
$script:CleanupInspections = $null
$script:CleanupContextCalls = 0
$script:CleanupUnexpectedCommand = $false
$passed = 0

function Assert-CleanupRegression {
    param([bool] $Condition)
    $script:CleanupChecks++
    if (-not $Condition) { throw ('E2E_RUNTIME_CLEANUP_REGRESSION_FAILED:' + $script:CleanupCase + ':' + $script:CleanupChecks) }
}

function New-CleanupVolume {
    param([string] $Name = 'reawote-e2e-postgres-data', [string] $Project = 'reawote-e2e')
    return [pscustomobject]@{
        Name = $Name; Driver = 'local'; Scope = 'local'; Options = $null
        Labels = [pscustomobject]@{ 'com.docker.compose.project' = $Project; 'com.docker.compose.volume' = 'postgres_data' }
        synthetic_data_sentinel = 'must-survive-both-runs'
        Mountpoint = $script:CleanupSecret; raw_untrusted_data = $script:CleanupSecret
    }
}

function New-CleanupContainer {
    param([string] $Service, [string] $Id, [string] $Project = 'reawote-e2e')
    $mounts = @()
    if ($Service -eq 'database') {
        $mounts = @([pscustomobject]@{
            Type = 'volume'; Name = 'reawote-e2e-postgres-data'; Driver = 'local'; Destination = '/var/lib/postgresql'; RW = $true
            Source = $script:CleanupSecret
        })
    }
    return [pscustomobject]@{
        Id = $Id; Name = "/$Project-$Service-1"; Mounts = $mounts
        Config = [pscustomobject]@{
            Labels = [pscustomobject]@{
                'com.docker.compose.project' = $Project; 'com.docker.compose.service' = $Service
                'com.docker.compose.container-number' = '1'; 'com.docker.compose.oneoff' = 'False'
            }
            Env = @($script:CleanupSecret)
        }
    }
}

function Add-CleanupRuntime {
    $script:CleanupFixture.Containers[('1' * 64)] = New-CleanupContainer 'database' ('1' * 64)
    $script:CleanupFixture.Containers[('2' * 64)] = New-CleanupContainer 'backend' ('2' * 64)
    $script:CleanupFixture.Containers[('3' * 64)] = New-CleanupContainer 'frontend' ('3' * 64)
    $attachments = [pscustomobject]@{}
    foreach ($id in @(('1' * 64), ('2' * 64), ('3' * 64))) {
        $attachments | Add-Member NoteProperty $id ([pscustomobject]@{ Name = $script:CleanupFixture.Containers[$id].Name })
    }
    $script:CleanupFixture.Networks[('4' * 64)] = [pscustomobject]@{
        Id = ('4' * 64); Name = 'reawote-e2e_default'; Driver = 'bridge'; Scope = 'local'
        Labels = [pscustomobject]@{ 'com.docker.compose.project' = 'reawote-e2e'; 'com.docker.compose.network' = 'default' }
        Options = [pscustomobject]@{}; Containers = $attachments; raw_untrusted_data = $script:CleanupSecret
    }
}

function New-CleanupFixture {
    $fixture = @{ Containers = @{}; Networks = @{}; Volumes = @{} }
    # Protected projects are deliberately present in every inventory. Their
    # names/labels do not select them, and their serialized state must not change.
    $fixture.Containers[('a' * 64)] = New-CleanupContainer 'database' ('a' * 64) 'reawote'
    $fixture.Containers[('b' * 64)] = New-CleanupContainer 'database' ('b' * 64) 'reawote-demo'
    foreach ($entry in @(@('c', 'reawote'), @('d', 'reawote-demo'))) {
        $fixture.Networks[($entry[0] * 64)] = [pscustomobject]@{
            Id = ($entry[0] * 64); Name = ($entry[1] + '_default')
            Labels = [pscustomobject]@{ 'com.docker.compose.project' = $entry[1] }
        }
    }
    $fixture.Volumes['reawote_postgres_data'] = New-CleanupVolume 'reawote_postgres_data' 'reawote'
    $fixture.Volumes['reawote-demo-postgres-data'] = New-CleanupVolume 'reawote-demo-postgres-data' 'reawote-demo'
    return $fixture
}

function Get-CleanupProtectedFingerprint {
    return ConvertTo-Json -InputObject @(
        $script:CleanupFixture.Containers[('a' * 64)], $script:CleanupFixture.Containers[('b' * 64)],
        $script:CleanupFixture.Networks[('c' * 64)], $script:CleanupFixture.Networks[('d' * 64)],
        $script:CleanupFixture.Volumes['reawote_postgres_data'], $script:CleanupFixture.Volumes['reawote-demo-postgres-data']
    ) -Depth 20 -Compress
}

function Invoke-E2eReadCommand {
    param($Executable, $Arguments, $OperationId)
    Set-E2eRunnerOperation -OperationId $OperationId
    Assert-CleanupRegression ($Executable -ceq 'docker')
    $command = $Arguments -join '|'
    if ($Arguments.Count -eq 6 -and
        ($command.StartsWith('ps|--all|--no-trunc|--quiet|--filter|') -or
         $command.StartsWith('network|ls|--no-trunc|--quiet|--filter|'))) {
        $items = if ($Arguments[0] -ceq 'ps') { $script:CleanupFixture.Containers.Values } else { $script:CleanupFixture.Networks.Values }
        $filter = $Arguments[5]
        Assert-CleanupRegression ($filter -cin @('label=com.docker.compose.project=reawote-e2e', 'name=^/?reawote-e2e'))
        foreach ($item in $items) {
            $labels = if ($Arguments[0] -ceq 'ps') { $item.Config.Labels } else { $item.Labels }
            $project = Get-E2eObjectPropertyValue $labels 'com.docker.compose.project'
            if (($filter.StartsWith('label=') -and $project -ceq 'reawote-e2e') -or
                ($filter.StartsWith('name=') -and $item.Name -cmatch '\A/?reawote-e2e')) { $item.Id }
        }
        return
    }
    if ($command -ceq 'volume|ls|--format|{{.Name}}') {
        return @($script:CleanupFixture.Volumes.Keys | Sort-Object)
    }
    if ($command -ceq 'volume|ls|--filter|label=com.docker.compose.project=reawote-e2e|--format|{{.Name}}') {
        foreach ($volume in $script:CleanupFixture.Volumes.Values) {
            if ((Get-E2eObjectPropertyValue $volume.Labels 'com.docker.compose.project') -ceq 'reawote-e2e') { $volume.Name }
        }
        return
    }
    if ($Arguments.Count -eq 3 -and $Arguments[1] -ceq 'inspect' -and $Arguments[0] -cin @('container', 'network', 'volume')) {
        $kind = $Arguments[0]; $id = $Arguments[2]
        $collection = switch ($kind) { container { $script:CleanupFixture.Containers }; network { $script:CleanupFixture.Networks }; volume { $script:CleanupFixture.Volumes } }
        Assert-CleanupRegression ($collection.ContainsKey($id))
        if ($kind -eq 'volume') { Assert-CleanupRegression ($id -ceq 'reawote-e2e-postgres-data') }
        else { Assert-CleanupRegression ($id -cmatch '\A[1-4]{64}\z') }
        $key = "$kind/$id"
        if (-not $script:CleanupInspections.ContainsKey($key)) { $script:CleanupInspections[$key] = 0 }
        $script:CleanupInspections[$key]++
        if ($script:CleanupCase -eq 'invalid_inspect_json' -and $kind -eq 'volume') { return ('{' + $script:CleanupSecret) }
        $inspection = $collection[$id]
        if ($script:CleanupCase -eq 'changed_inspection_id' -and $kind -eq 'container' -and $script:CleanupInspections[$key] -ge 2) {
            $inspection = $inspection | ConvertTo-Json -Depth 20 | ConvertFrom-Json
            $inspection.Id = 'f' * 64
        }
        # This is actual Docker's root-array wire shape, not a mock object shortcut.
        return ConvertTo-Json -InputObject @($inspection) -Depth 20 -Compress
    }
    $script:CleanupUnexpectedCommand = $true
    throw $script:CleanupSecret
}

function Assert-LocalDockerContext {
    $script:CleanupContextCalls++
    if ($script:CleanupCase -eq 'ownership_changed_before_delete') {
        $script:CleanupFixture.Containers[('1' * 64)].Config.Labels.'com.docker.compose.project' = 'foreign-project'
    }
    return 'synthetic-local-context'
}

function Resolve-E2eReadExecutable {
    param($Executable, $OperationId)
    Assert-CleanupRegression ($Executable -ceq 'docker' -and $OperationId -ceq 'docker_mutation_executable_resolution')
    Set-E2eRunnerOperation -OperationId $OperationId
    return 'synthetic-docker-executable'
}

function Invoke-E2eRunnerPrivateProcess {
    param($FilePath, $Arguments, $OperationId)
    Assert-CleanupRegression ($FilePath -ceq 'synthetic-docker-executable')
    Set-E2eRunnerOperation -OperationId $OperationId
    $kind = $Arguments[0]
    if ($kind -ceq 'container') {
        Assert-CleanupRegression ($Arguments.Count -eq 4 -and $Arguments[1] -ceq 'rm' -and $Arguments[2] -ceq '--force')
        $id = $Arguments[3]
        Assert-CleanupRegression ($OperationId -ceq 'cleanup_container_remove')
    }
    elseif ($kind -ceq 'network') {
        Assert-CleanupRegression ($Arguments.Count -eq 3 -and $Arguments[1] -ceq 'rm')
        $id = $Arguments[2]
        Assert-CleanupRegression ($OperationId -ceq 'cleanup_network_remove')
    }
    else { throw 'E2E_RUNTIME_CLEANUP_FORBIDDEN_MUTATION' }
    Assert-CleanupRegression ($id -cmatch '\A[1-4]{64}\z')
    Assert-CleanupRegression ($script:CleanupInspections["$kind/$id"] -ge 2)
    Assert-CleanupRegression ($script:CleanupContextCalls -gt $script:CleanupMutations.Count)
    $script:CleanupMutations.Add(($Arguments -join '|'))
    if ($kind -eq 'container') {
        $script:CleanupFixture.Containers.Remove($id)
        foreach ($network in $script:CleanupFixture.Networks.Values) {
            $attachments = Get-E2eObjectPropertyValue $network 'Containers'
            if ($null -ne $attachments) { $attachments.PSObject.Properties.Remove($id) }
        }
    }
    else { $script:CleanupFixture.Networks.Remove($id) }
    $script:E2eDiagnostics.process_started = $true
    $script:E2eDiagnostics.exit_code = 0
}

$cases = @(
    @{ Name = 'no_previous_resources'; Volume = $false; Runtime = $false; Mutations = 0 },
    @{ Name = 'programmer_a_safe_retained_volume'; Volume = $true; Runtime = $false; Mutations = 0 },
    @{ Name = 'safe_volume_empty_options_object'; Volume = $true; Runtime = $false; Mutations = 0 },
    @{ Name = 'safe_old_runtime'; Volume = $true; Runtime = $true; Mutations = 4 },
    @{ Name = 'safe_legacy_container_names'; Volume = $true; Runtime = $true; Mutations = 4 },
    @{ Name = 'two_cleanup_cycles_same_volume'; Volume = $true; Runtime = $true; Mutations = 8 },
    @{ Name = 'foreign_same_name'; Operation = 'container_ownership_validation' },
    @{ Name = 'foreign_similar_name'; Operation = 'container_ownership_validation' },
    @{ Name = 'container_missing_project_label'; Operation = 'container_ownership_validation' },
    @{ Name = 'container_wrong_service_label'; Operation = 'container_ownership_validation' },
    @{ Name = 'container_missing_number_label'; Operation = 'container_ownership_validation' },
    @{ Name = 'database_bind_mount'; Operation = 'container_mount_validation' },
    @{ Name = 'unexpected_backend_mount'; Operation = 'container_mount_validation' },
    @{ Name = 'volume_driver'; Operation = 'volume_driver_validation' },
    @{ Name = 'volume_scope'; Operation = 'volume_scope_validation' },
    @{ Name = 'volume_options'; Operation = 'volume_options_validation' },
    @{ Name = 'volume_missing_options'; Operation = 'volume_options_validation' },
    @{ Name = 'volume_wrong_project_label'; Operation = 'volume_ownership_validation' },
    @{ Name = 'volume_wrong_volume_label'; Operation = 'volume_ownership_validation' },
    @{ Name = 'volume_missing_project_label'; Operation = 'volume_ownership_validation' },
    @{ Name = 'volume_similar_name'; Operation = 'volume_name_validation' },
    @{ Name = 'network_wrong_project_label'; Operation = 'network_ownership_validation' },
    @{ Name = 'network_missing_label'; Operation = 'network_ownership_validation' },
    @{ Name = 'network_foreign_attachment'; Operation = 'network_attachment_validation' },
    @{ Name = 'ownership_changed_before_delete'; Operation = 'container_ownership_validation' },
    @{ Name = 'changed_inspection_id'; Operation = 'container_ownership_validation' },
    @{ Name = 'invalid_inspect_json'; Operation = 'runtime_inventory_parse'; Category = 'invalid_output' }
)

foreach ($case in $cases) {
    $script:CleanupCase = $case.Name
    $script:CleanupFixture = New-CleanupFixture
    $script:CleanupMutations = [Collections.Generic.List[string]]::new()
    $script:CleanupInspections = @{}
    $script:CleanupContextCalls = 0
    $script:CleanupUnexpectedCommand = $false
    $unsafe = $case.ContainsKey('Operation')
    if ($unsafe -or $case.Volume) { $script:CleanupFixture.Volumes['reawote-e2e-postgres-data'] = New-CleanupVolume }
    if ($unsafe -or $case.Runtime) { Add-CleanupRuntime }
    $volume = if ($script:CleanupFixture.Volumes.ContainsKey('reawote-e2e-postgres-data')) { $script:CleanupFixture.Volumes['reawote-e2e-postgres-data'] } else { $null }
    switch ($case.Name) {
        'safe_volume_empty_options_object' { $volume.Options = [pscustomobject]@{} }
        'safe_legacy_container_names' {
            foreach ($id in @(('1' * 64), ('2' * 64), ('3' * 64))) {
                $container = $script:CleanupFixture.Containers[$id]
                $container.Name = '/reawote-e2e_' + $container.Config.Labels.'com.docker.compose.service' + '_1'
            }
        }
        'foreign_same_name' { $script:CleanupFixture.Containers[('1' * 64)].Config.Labels.'com.docker.compose.project' = 'reawote' }
        'foreign_similar_name' {
            $script:CleanupFixture.Containers[('1' * 64)].Name = '/reawote-e2e-database-1-foreign'
            $script:CleanupFixture.Containers[('1' * 64)].Config.Labels.'com.docker.compose.project' = 'foreign-project'
        }
        'container_missing_project_label' { $script:CleanupFixture.Containers[('1' * 64)].Config.Labels.PSObject.Properties.Remove('com.docker.compose.project') }
        'container_wrong_service_label' { $script:CleanupFixture.Containers[('1' * 64)].Config.Labels.'com.docker.compose.service' = 'unrecognized' }
        'container_missing_number_label' { $script:CleanupFixture.Containers[('1' * 64)].Config.Labels.PSObject.Properties.Remove('com.docker.compose.container-number') }
        'database_bind_mount' { $script:CleanupFixture.Containers[('1' * 64)].Mounts[0].Type = 'bind' }
        'unexpected_backend_mount' { $script:CleanupFixture.Containers[('2' * 64)].Mounts = @([pscustomobject]@{ Type = 'bind'; Source = $script:CleanupSecret }) }
        'volume_driver' { $volume.Driver = 'remote-driver' }
        'volume_scope' { $volume.Scope = 'global' }
        'volume_options' { $volume.Options = [pscustomobject]@{ type = 'nfs'; device = $script:CleanupSecret } }
        'volume_missing_options' { $volume.PSObject.Properties.Remove('Options') }
        'volume_wrong_project_label' { $volume.Labels.'com.docker.compose.project' = 'reawote-demo' }
        'volume_wrong_volume_label' { $volume.Labels.'com.docker.compose.volume' = 'foreign-data' }
        'volume_missing_project_label' { $volume.Labels.PSObject.Properties.Remove('com.docker.compose.project') }
        'volume_similar_name' { $script:CleanupFixture.Volumes['reawote-e2e-postgres-data-foreign'] = New-CleanupVolume 'reawote-e2e-postgres-data-foreign' 'foreign-project' }
        'network_wrong_project_label' { $script:CleanupFixture.Networks[('4' * 64)].Labels.'com.docker.compose.project' = 'reawote-demo' }
        'network_missing_label' { $script:CleanupFixture.Networks[('4' * 64)].Labels.PSObject.Properties.Remove('com.docker.compose.network') }
        'network_foreign_attachment' { $script:CleanupFixture.Networks[('4' * 64)].Containers | Add-Member NoteProperty ('f' * 64) ([pscustomobject]@{ Name = $script:CleanupSecret }) }
    }
    $protectedBefore = Get-CleanupProtectedFingerprint
    $volumesBefore = ConvertTo-Json -InputObject $script:CleanupFixture.Volumes -Depth 20 -Compress
    $script:E2eDiagnostics = New-E2ePhaseDiagnosticsState
    Start-E2eRunnerPhase -Phase 'compose_config_validation' -OperationId 'compose_config_validation'
    Complete-E2eRunnerPhase
    Start-E2eRunnerPhase -Phase 'previous_runtime_cleanup' -OperationId 'runtime_inventory'
    $failure = $null
    $output = [Collections.Generic.List[object]]::new()
    try {
        & {
            Invoke-E2eRuntimeCleanup
            if ($case.Name -eq 'two_cleanup_cycles_same_volume') {
                # Simulate the next runner's disposable runtime, keeping the
                # exact same volume object and synthetic persisted data sentinel.
                Add-CleanupRuntime
                Invoke-E2eRuntimeCleanup
            }
        } 2>&1 3>&1 4>&1 5>&1 6>&1 | ForEach-Object { $output.Add($_) }
    }
    catch {
        $failure = $_.Exception.Message
        if ($null -eq $script:E2eDiagnostics.error_category) {
            Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'validation_failure'
        }
    }
    Assert-CleanupRegression ($output.Count -eq 0)
    Assert-CleanupRegression (-not $script:CleanupUnexpectedCommand)
    Assert-CleanupRegression ((Get-CleanupProtectedFingerprint) -ceq $protectedBefore)
    Assert-CleanupRegression ((ConvertTo-Json -InputObject $script:CleanupFixture.Volumes -Depth 20 -Compress) -ceq $volumesBefore)
    if ($unsafe) {
        Assert-CleanupRegression ($failure -ceq 'E2E_SAFE_OPERATION_FAILED')
        Assert-CleanupRegression ($script:CleanupMutations.Count -eq 0)
        Assert-CleanupRegression ($script:E2eDiagnostics.failure_snapshot.current_phase -ceq 'previous_runtime_cleanup')
        Assert-CleanupRegression ($script:E2eDiagnostics.failure_snapshot.operation_id -ceq $case.Operation)
        $expectedCategory = if ($case.ContainsKey('Category')) { $case.Category } else { 'validation_failure' }
        Assert-CleanupRegression ($script:E2eDiagnostics.failure_snapshot.error_category -ceq $expectedCategory)
    }
    else {
        Assert-CleanupRegression ($null -eq $failure -and $null -eq $script:E2eDiagnostics.failure_snapshot)
        Assert-CleanupRegression ($script:CleanupMutations.Count -eq $case.Mutations)
        Assert-CleanupRegression ($script:CleanupFixture.Containers.Count -eq 2 -and $script:CleanupFixture.Networks.Count -eq 2)
        if ($case.Mutations -eq 0) { Assert-CleanupRegression ($script:CleanupContextCalls -eq 0) }
    }
    foreach ($mutation in $script:CleanupMutations) {
        Assert-CleanupRegression ($mutation -cmatch '\A(?:container\|rm\|--force\|[1-3]{64}|network\|rm\|4{64})\z')
        Assert-CleanupRegression ($mutation -cnotmatch 'volume|compose|prune|reawote|down|--volumes|\|-v(?:\||$)')
    }
    $safe = ConvertTo-E2eSafeDiagnostics -State $script:E2eDiagnostics -ErrorCode 'E2E_ISOLATION_FAILED'
    $keys = @($safe -split '\r?\n' | ForEach-Object { ($_ -split '=', 2)[0] })
    Assert-CleanupRegression (($keys -join ',') -ceq 'schema_version,error_code,last_completed_phase,current_phase,failed_phase,operation_id,error_category,process_started,exit_code,system_error_code,log_check_status')
    Assert-CleanupRegression (-not $safe.Contains($script:CleanupSecret))
    Assert-CleanupRegression (-not $safe.Contains($repositoryRoot))
    Assert-CleanupRegression ($safe -cnotmatch 'Mountpoint|DockerHost|DOCKER_HOST|stdout|stderr|Options|Exception|synthetic_data_sentinel')
    $passed++
}

Write-Host "E2E runtime cleanup: $passed passed, 0 skipped, 0 failed; $script:CleanupChecks assertions; Docker boundaries mocked, no real Docker mutations."
