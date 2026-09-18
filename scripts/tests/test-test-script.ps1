$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$testScript = Join-Path (Split-Path -Parent $PSScriptRoot) 'test.ps1'
$state = [pscustomobject]@{
    Calls = [Collections.Generic.List[string]]::new()
    Mode = ''; Project = ''; Endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
}
function docker {
    $command = $args -join ' '
    $state.Calls.Add($command)
    $global:LASTEXITCODE = 0
    if ($command -eq 'context show') { 'desktop-linux'; return }
    if ($command -eq 'context inspect desktop-linux') {
        '[{"Endpoints":{"docker":{"Host":"' + $state.Endpoint + '"}}}]'; return
    }
    if ($command -eq "info --format {{.OSType}}") {
        if ($state.Mode -eq 'windows-engine') { 'windows' } else { 'linux' }; return
    }
    if ($command -match '^ps .*project=(reawote-test-[a-f0-9]{32})$') {
        $state.Project = $Matches[1]
        if ($state.Mode -eq 'collision') { 'existing-container' }; return
    }
    if ($command -match '^volume ls ') { return }
    if ($command -match '^compose ') {
        if (-not $command.Contains('--project-name ' + $state.Project + ' ')) { throw 'Project escaped isolation.' }
        if (-not $command.Contains(' --env-file ') -or -not $command.Contains(' -f ')) { throw 'Compose files must be pinned.' }
        if ($command -match 'RUN_POSTGRES_TESTS=1') {
            if ($state.Mode -in @('success', 'pg-only')) { 'collected=20, executed=20, passed=20, skipped=0, failed=0' }
            else { $global:LASTEXITCODE = 1; 'mandatory auth gate failed' }
        }
        return
    }
    throw "Unexpected Docker command: $command"
}
$previousHost = $env:DOCKER_HOST
$previousPassword = $env:POSTGRES_PASSWORD
$projects = [Collections.Generic.HashSet[string]]::new()
try {
    $env:DOCKER_HOST = $null
    foreach ($mode in @('success', 'pg-only', 'pg-only-failure', 'auth-failure', 'auth-skip', 'no-auth-tests', 'collision', 'remote-context', 'remote-env', 'windows-engine')) {
        $state.Mode = $mode; $state.Calls.Clear(); $state.Project = ''
        $state.Endpoint = if ($mode -eq 'remote-context') { 'ssh://remote.example' } else { 'npipe:////./pipe/dockerDesktopLinuxEngine' }
        $env:DOCKER_HOST = if ($mode -eq 'remote-env') { 'tcp://127.0.0.1:2375' } else { $null }
        $failure = $null
        try { & $testScript -PostgresqlOnly:($mode.StartsWith('pg-only')) | Out-Null } catch { $failure = $_.Exception.Message }
        if (($null -eq $failure) -ne ($mode -in @('success', 'pg-only'))) { throw "Unexpected result for ${mode}: $failure" }
        $mutations = @($state.Calls | Where-Object { $_ -match '^compose .* (build|up|run|down)( |$)' })
        $blocked = $mode -in @('collision', 'remote-context', 'remote-env', 'windows-engine')
        if ($blocked -and $mutations.Count -ne 0) { throw 'Unsafe mutation before validation.' }
        if (-not $blocked) {
            if (-not $projects.Add($state.Project)) { throw 'Reused test namespace.' }
            $gate = @($mutations | Where-Object { $_ -match 'RUN_POSTGRES_TESTS=1.*--require-auth-postgresql.*test_auth_postgresql.py.*test_materials_postgresql.py' })
            if ($gate.Count -ne 1) { throw 'Mandatory PostgreSQL gate missing.' }
            if (-not $gate[0].Contains('test_packaging_dispatch_lease_postgresql.py')) { throw 'Dispatch PostgreSQL tests missing.' }
            if (-not $gate[0].Contains('test_material_lifecycle_postgresql.py')) { throw 'Lifecycle PostgreSQL tests missing.' }
            if ($mode.StartsWith('pg-only')) {
                if (@($mutations | Where-Object { $_ -match ' backend pytest --ignore| worker pytest| frontend npm' }).Count) {
                    throw 'PostgreSQL-only run entered an unrelated phase.'
                }
                if (@($mutations | Where-Object { $_ -match ' build backend$' }).Count -ne 1) { throw 'PostgreSQL-only build is not bounded to backend.' }
            }
            $cleanup = @($mutations | Where-Object { $_ -match ' down --remove-orphans$' })
            if ($cleanup.Count -ne 1) { throw 'Owned container cleanup missing.' }
            $worker = @($mutations | Where-Object { $_ -match ' worker pytest$' })
            if (($worker.Count -eq 1) -ne ($mode -eq 'success')) { throw 'Failed gate did not stop later phases.' }
        }
        if (@($state.Calls | Where-Object { $_ -match '(--volumes|prune|volume rm)' }).Count) { throw 'Destructive volume operation.' }
        if ($env:POSTGRES_PASSWORD -ne $previousPassword) { throw 'Environment was not restored.' }
        Write-Host "PASS: $mode"
    }
    Write-Host 'Test runner isolation: 10 passed, 0 skipped, 0 failed.'
}
finally { $env:DOCKER_HOST = $previousHost }
