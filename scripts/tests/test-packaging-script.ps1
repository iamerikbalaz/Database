$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$runner = Join-Path (Split-Path -Parent $PSScriptRoot) 'test-packaging.ps1'
$state = [pscustomobject]@{ Calls = [Collections.Generic.List[string]]::new(); Mode = ''; Name = ''; Built = $false; Ran = $false }
function docker {
    $command = $args -join ' '; $state.Calls.Add($command); $global:LASTEXITCODE = 0
    if ($command -eq 'context show') { 'desktop-linux'; return }
    if ($command -eq 'context inspect desktop-linux') {
        $endpoint = if ($state.Mode -eq 'remote-context' -or ($state.Mode -eq 'context-changed' -and $state.Built)) { 'ssh://synthetic.invalid' } else { 'npipe:////./pipe/dockerDesktopLinuxEngine' }
        '[{"Endpoints":{"docker":{"Host":"' + $endpoint + '"}}}]'; return
    }
    if ($command -eq 'info --format {{.OSType}}') { if ($state.Mode -eq 'windows-engine') { 'windows' } else { 'linux' }; return }
    if ($command -match '^ps --all --quiet --filter name=\^/(reawote-packaging-[a-f0-9]{32})\$$') {
        $state.Name = $Matches[1]
        if ($state.Mode -eq 'container-collision' -or ($state.Ran -and $state.Mode -in @('leftover', 'wrong-owner'))) { 'synthetic-owned-id' }
        return
    }
    if ($command -eq ('image ls --quiet ' + $state.Name + ':test')) { if ($state.Mode -eq 'image-collision') { 'synthetic-image' }; return }
    if ($command -match '^build --tag ') {
        if (-not $command.StartsWith('build --tag ' + $state.Name + ':test --file ')) { throw 'Build escaped namespace.' }
        $state.Built = $true
        if ($state.Mode -eq 'build-failure') { $global:LASTEXITCODE = 1 }; return
    }
    if ($command -match '^run ') {
        $state.Ran = $true
        foreach ($required in @(('--rm --name ' + $state.Name), ('--label com.reawote.packaging-test=' + $state.Name),
            '--network none', '--read-only', '--cap-drop ALL', '--security-opt no-new-privileges', '--user 65532:65532',
            '--memory 4g', '--cpus 2', '--pids-limit 256', '--tmpfs /tmp:rw,noexec,nosuid,size=2g', '--env REQUIRE_PACKAGING_RUNTIME=1',
            ($state.Name + ':test python -m pytest -p no:cacheprovider --basetemp /tmp/pytest --tb=short'))) {
            if (-not $command.Contains($required)) { throw "Missing required isolation/gate: $required" }
        }
        $dockerOptions = $command.Substring(0, $command.IndexOf($state.Name + ':test'))
        if ($dockerOptions -match '(--mount|--volume|--publish|--privileged|--env-file| -v | -p )') { throw 'Unexpected host access.' }
        if ($state.Mode -in @('test-failure', 'leftover', 'wrong-owner')) { $global:LASTEXITCODE = 1 }; return
    }
    if ($command -match '^inspect --format .* synthetic-owned-id$') {
        if ($state.Mode -eq 'wrong-owner') { 'unrelated-owner' } else { $state.Name }; return
    }
    if ($command -eq 'rm --force synthetic-owned-id' -and $state.Mode -eq 'leftover') { return }
    throw "Unexpected Docker operation: $command"
}
$previousHost = $env:DOCKER_HOST
try {
    foreach ($mode in @('success', 'test-failure', 'build-failure', 'container-collision', 'image-collision', 'remote-context', 'remote-env', 'windows-engine', 'context-changed', 'leftover', 'wrong-owner')) {
        $state.Mode = $mode; $state.Calls.Clear(); $state.Name = ''; $state.Built = $false; $state.Ran = $false
        $env:DOCKER_HOST = if ($mode -eq 'remote-env') { 'tcp://127.0.0.1:2375' } else { $null }
        $failure = $null
        try { & $runner | Out-Null } catch { $failure = $_.Exception.Message }
        if (($null -eq $failure) -ne ($mode -eq 'success')) { throw "Unexpected result for ${mode}: $failure" }
        $blocked = $mode -in @('container-collision', 'image-collision', 'remote-context', 'remote-env', 'windows-engine')
        if ($blocked -and ($state.Built -or $state.Ran)) { throw 'Mutation before safety validation.' }
        if ($mode -in @('context-changed', 'build-failure') -and $state.Ran) { throw 'Failure did not stop container creation.' }
        if ($mode -in @('success', 'test-failure', 'leftover', 'wrong-owner') -and -not $state.Ran) { throw 'Test phase was never reached.' }
        if (@($state.Calls | Where-Object { $_ -match '^rm ' }).Count -ne [int]($mode -eq 'leftover')) { throw 'Unsafe or missing owned cleanup.' }
        if (@($state.Calls | Where-Object { $_ -match '(prune|compose|volume rm|image rm)' }).Count) { throw 'Unexpected resource mutation.' }
        Write-Host "PASS: $mode"
    }
    Write-Host 'Packaging runner isolation: 11 passed, 0 skipped, 0 failed.'
}
finally { $env:DOCKER_HOST = $previousHost }
