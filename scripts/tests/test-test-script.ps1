$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Runs the real orchestration script against a mock command; never starts Docker.
$testScript = Join-Path (Split-Path -Parent $PSScriptRoot) 'test.ps1'
$reawoteTestDockerState = [pscustomobject]@{
    Calls = [Collections.Generic.List[string]]::new()
    DatabaseInitiallyRunning = $false
    PostgresExitCode = 0
    PostgresSummary = ''
}

function docker {
    $command = $args -join ' '
    $reawoteTestDockerState.Calls.Add($command)
    $global:LASTEXITCODE = 0
    if ($command -eq 'compose ps --status running --services') {
        if ($reawoteTestDockerState.DatabaseInitiallyRunning) { 'database' }
    }
    elseif ($command -match 'RUN_POSTGRES_TESTS=1') {
        $reawoteTestDockerState.PostgresSummary
        $global:LASTEXITCODE = $reawoteTestDockerState.PostgresExitCode
    }
}

$scenarios = @(
    @{ Name = 'success'; Code = 0; Summary = 'collected=19, executed=19, passed=19, skipped=0, failed=0' },
    @{ Name = 'auth failure'; Code = 1; Summary = 'collected=19, executed=19, passed=18, skipped=0, failed=1' },
    @{ Name = 'auth skip'; Code = 1; Summary = 'collected=19, executed=18, passed=18, skipped=1, failed=0' },
    @{ Name = 'no auth tests'; Code = 1; Summary = 'collected=0, executed=0, passed=0, skipped=0, failed=0' }
)
$passed = 0
foreach ($initiallyRunning in @($false, $true)) {
    foreach ($scenario in $scenarios) {
        $reawoteTestDockerState.Calls.Clear()
        $reawoteTestDockerState.DatabaseInitiallyRunning = $initiallyRunning
        $reawoteTestDockerState.PostgresExitCode = $scenario.Code
        $reawoteTestDockerState.PostgresSummary = "Auth PostgreSQL gate: $($scenario.Summary)"
        $failure = $null
        $output = [Collections.Generic.List[string]]::new()
        try {
            & $testScript *>&1 | ForEach-Object { $output.Add([string]$_) }
        }
        catch { $failure = $_.Exception.Message }

        if ($scenario.Code -eq 0) {
            if ($null -ne $failure) { throw "Success unexpectedly failed: $failure" }
        }
        elseif ($failure -ne 'PostgreSQL integration tests failed with exit code 1.') {
            throw "PostgreSQL failure was not propagated: $failure"
        }

        $expectedPostgres = 'compose run --rm --no-deps -e RUN_POSTGRES_TESTS=1 backend pytest --require-auth-postgresql tests/test_auth_postgresql.py tests/test_materials_postgresql.py'
        if (@($reawoteTestDockerState.Calls | Where-Object { $_ -eq $expectedPostgres }).Count -ne 1) {
            throw 'PostgreSQL auth + material command and mandatory guard must run exactly once.'
        }
        if (-not ($output -contains $reawoteTestDockerState.PostgresSummary)) {
            throw 'Actual auth PostgreSQL execution counts were not printed.'
        }
        $stopCount = @($reawoteTestDockerState.Calls | Where-Object { $_ -eq 'compose stop database' }).Count
        $expectedStopCount = if ($initiallyRunning) { 0 } else { 1 }
        if ($stopCount -ne $expectedStopCount) { throw 'Database cleanup ownership changed.' }
        $workerCount = @($reawoteTestDockerState.Calls | Where-Object { $_ -eq 'compose run --rm --no-deps worker pytest' }).Count
        $expectedWorkerCount = if ($scenario.Code -eq 0) { 1 } else { 0 }
        if ($workerCount -ne $expectedWorkerCount) { throw 'A failed PostgreSQL gate did not stop later phases.' }
        foreach ($command in $reawoteTestDockerState.Calls) {
            if ($command -match '(^| )(down|volume|prune|-v|--volumes|-p|--project-name)( |$)' -or
                $command -match '(demo|e2e)') {
                throw "Unsafe or unrelated Docker operation: $command"
            }
        }
        $passed++
        Write-Host "PASS: $($scenario.Name), database previously running=$initiallyRunning"
    }
}
Write-Host "scripts/test.ps1 regression tests: $passed passed, 0 skipped, 0 failed."
