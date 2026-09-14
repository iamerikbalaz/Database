$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')

function Assert-Throws {
    param([Parameter(Mandatory)] [scriptblock] $Action, [Parameter(Mandatory)] [string] $Name)
    try { & $Action }
    catch { Write-Host "PASS: $Name"; return }
    throw "Expected failure did not occur: $Name"
}

function Assert-Equal {
    param($Actual, $Expected, [string] $Name)
    if ($Actual -ne $Expected) { throw "$Name expected '$Expected', got '$Actual'." }
    Write-Host "PASS: $Name"
}

function Assert-ThrowsWithMessage {
    param(
        [Parameter(Mandatory)] [scriptblock] $Action,
        [Parameter(Mandatory)] [string[]] $ExpectedFragments,
        [Parameter(Mandatory)] [string] $Name,
        [string[]] $ForbiddenFragments = @()
    )
    try { & $Action }
    catch {
        $message = $_.Exception.Message
        foreach ($fragment in $ExpectedFragments) {
            if (-not $message.Contains($fragment)) {
                throw "$Name expected error fragment '$fragment', got '$message'."
            }
        }
        foreach ($fragment in $ForbiddenFragments) {
            if ($message.Contains($fragment)) {
                throw "$Name exposed forbidden error fragment '$fragment'."
            }
        }
        Write-Host "PASS: $Name"
        return
    }
    throw "Expected failure did not occur: $Name"
}

$repositoryRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runsRoot = Join-Path $repositoryRoot '.e2e-data\runs'
$createdRuns = [Collections.Generic.List[object]]::new()
$junctions = [Collections.Generic.List[string]]::new()
$sentinels = [Collections.Generic.List[string]]::new()

try {
    $firstSyntheticPassword = New-E2eSyntheticPassword
    $secondSyntheticPassword = New-E2eSyntheticPassword
    Assert-Equal ($firstSyntheticPassword -match '^E2E![0-9a-f]{64}$') $true 'synthetic password uses the constrained ASCII format'
    Assert-Equal ($secondSyntheticPassword -match '^E2E![0-9a-f]{64}$') $true 'second synthetic password uses the constrained ASCII format'
    Assert-Equal ($firstSyntheticPassword -ne $secondSyntheticPassword) $true 'synthetic passwords are unique per generation'

    $diagnosticSecret = New-E2eSyntheticPassword
    $safeDiagnostic = @(
        "safe service status",
        "password=$diagnosticSecret",
        'response header set-cookie: must-not-survive',
        'safe cleanup status'
    ) -join [Environment]::NewLine
    $protectedDiagnostic = Protect-E2eDiagnosticText -Text $safeDiagnostic -Secrets @($diagnosticSecret)
    Assert-Equal ($protectedDiagnostic.Contains($diagnosticSecret)) $false 'diagnostic protection removes exact generated secrets'
    Assert-Equal ($protectedDiagnostic.Contains('set-cookie: must-not-survive')) $false 'diagnostic protection removes sensitive authentication lines'
    Assert-Equal ($protectedDiagnostic.Contains('safe service status')) $true 'diagnostic protection preserves safe lines before a redaction'
    Assert-Equal ($protectedDiagnostic.Contains('safe cleanup status')) $true 'diagnostic protection preserves safe lines after a redaction'
    Assert-Equal (Test-E2eTextContainsSecret -Text "log $diagnosticSecret" -Secrets @($diagnosticSecret)) $true 'secret scan finds an exact generated secret'
    Assert-Equal (Test-E2eTextContainsSecret -Text 'csrf_token=must-not-survive') $true 'secret scan finds a sensitive authentication field'
    Assert-Equal (Test-E2eTextContainsSecret -Text 'ordinary synthetic service log') $false 'secret scan accepts ordinary service logs'

    $safeGuid = [guid]::NewGuid()
    $safeRun = New-E2eManagedRunDirectory $repositoryRoot $runsRoot $safeGuid
    $createdRuns.Add([pscustomobject]@{ Guid = $safeGuid; Path = $safeRun })
    Assert-Equal (Test-E2ePathWithinRoot $runsRoot $safeRun) $true 'safe local run root'

    # Do not put the unique value in argv, environment, fixture source or an
    # assertion message. The only input channel to the fixture is private stdin.
    $bootstrapSecret = New-E2eSyntheticPassword
    foreach ($mode in @('bootstrap-success', 'bootstrap-failure', 'workflow-failure', 'runner-failure')) {
        $probe = [Diagnostics.Process]::new()
        try {
            $probe.StartInfo.FileName = Join-Path $PSHOME 'powershell.exe'
            $probe.StartInfo.Arguments = (@(@(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                (Join-Path $PSScriptRoot 'e2e-bootstrap-regression-child.ps1'),
                '-Mode', $mode, '-RunRoot', $safeRun
            ) | ForEach-Object { ConvertTo-E2eProcessArgument $_ }) -join ' ')
            $probe.StartInfo.UseShellExecute = $false
            $probe.StartInfo.CreateNoWindow = $true
            $probe.StartInfo.RedirectStandardInput = $true
            $probe.StartInfo.RedirectStandardOutput = $true
            $probe.StartInfo.RedirectStandardError = $true
            [void]$probe.Start()
            $probeOutputTask = $probe.StandardOutput.ReadToEndAsync()
            $probeErrorTask = $probe.StandardError.ReadToEndAsync()
            $probe.StandardInput.WriteLine($bootstrapSecret)
            $probe.StandardInput.Close()
            if (-not $probe.WaitForExit(30000)) {
                $probe.Kill()
                throw 'E2E_REGRESSION_TIMEOUT: Child process did not complete.'
            }
            $probeOutput = $probeOutputTask.GetAwaiter().GetResult()
            $probeError = $probeErrorTask.GetAwaiter().GetResult()
            Assert-Equal $probe.ExitCode 0 "$mode child completed and removed inherited credentials"
            Assert-Equal ($probeOutput.Contains($bootstrapSecret)) $false "$mode complete stdout does not contain the synthetic secret"
            Assert-Equal ($probeError.Contains($bootstrapSecret)) $false "$mode complete stderr does not contain the synthetic secret"
            Assert-Equal (($probeOutput + $probeError) -match 'GetPassWarning|Password input may be echoed|Password:|raw stdout') $false "$mode emits neither bootstrap streams nor prompts"
            $artifactContent = [IO.File]::ReadAllText((Join-Path $safeRun "$mode.txt"))
            Assert-Equal ($artifactContent.Contains($bootstrapSecret)) $false "$mode complete diagnostic artifact does not contain the synthetic secret"
            if ($mode -eq 'bootstrap-success') {
                Assert-Equal ($probeOutput.Contains('E2E_REGRESSION_OK')) $true 'successful private bootstrap preserves the successful exit status'
            }
            if ($mode -eq 'bootstrap-failure') {
                Assert-Equal ($probeOutput.Contains('E2E_BOOTSTRAP_FAILED')) $true 'failed private bootstrap returns only its safe error code'
                Assert-Equal ($probeOutput.Contains('exit_code=23')) $true 'failed private bootstrap preserves only its numeric subprocess exit code'
            }
            if ($mode -eq 'runner-failure') {
                Assert-Equal ($probeOutput.Contains('error_code=E2E_ISOLATION_FAILED')) $true 'actual runner failure is converted to its allowlisted message'
                Assert-Equal ($probeOutput.Contains('operation_id=docker_context_policy_validation')) $true 'runner child is blocked at the real context policy guard'
                Assert-Equal ($probeOutput.Contains('error_category=policy_rejected')) $true 'runner child fails closed even on a Docker host'
                Assert-Equal ($probeOutput.Contains('process_started=false')) $true 'runner child blocks before any Docker process'
            }
        }
        finally { $probe.Dispose() }
    }
    Assert-E2eTreeNoReparse -RunRoot $safeRun
    foreach ($artifactFile in @(Get-ChildItem -LiteralPath $safeRun -File -Recurse -Force)) {
        Assert-Equal ([IO.File]::ReadAllText($artifactFile.FullName).Contains($bootstrapSecret)) $false 'complete regression artifact tree is free of the synthetic secret'
    }
    Assert-Equal (Get-E2eSafeFailureMessage -Code $bootstrapSecret) 'E2E_RUN_FAILED: The isolated E2E run could not complete.' 'unknown runner failure values never enter safe diagnostics'
    $bootstrapSecret = $null

    Assert-Throws { Assert-LocalE2eRepositoryRoot '\\server\share\repo' } 'UNC repository root'
    Assert-Throws { Assert-LocalE2eRepositoryRoot $repositoryRoot ([IO.DriveType]::Network) } 'network drive repository root'

    $prefixGuid = [guid]::NewGuid()
    $similarPrefix = Join-Path (Join-Path $repositoryRoot '.e2e-data\runs-other') $prefixGuid.ToString('D')
    Assert-Throws { Assert-E2eManagedRunPath $repositoryRoot $runsRoot $prefixGuid $similarPrefix } 'similar textual prefix outside runs root'

    $junctionCapability = $true
    $probeTarget = Join-Path $repositoryRoot ".e2e-data\junction-probe-target-$([guid]::NewGuid().ToString('N'))"
    $probeLink = Join-Path $repositoryRoot ".e2e-data\junction-probe-link-$([guid]::NewGuid().ToString('N'))"
    [void][IO.Directory]::CreateDirectory($probeTarget)
    try { [void](New-Item -ItemType Junction -Path $probeLink -Target $probeTarget -ErrorAction Stop) }
    catch { $junctionCapability = $false; Write-Warning "Junction tests skipped because junction creation is unavailable: $($_.Exception.Message)" }
    finally {
        if (Test-Path -LiteralPath $probeLink) { Remove-Item -LiteralPath $probeLink -Force }
        if (Test-Path -LiteralPath $probeTarget) { Remove-Item -LiteralPath $probeTarget -Force }
    }

    if ($junctionCapability) {
        $parentTarget = Join-Path $repositoryRoot ".e2e-data\parent-target-$([guid]::NewGuid().ToString('N'))"
        $parentLink = Join-Path $repositoryRoot ".e2e-data\parent-link-$([guid]::NewGuid().ToString('N'))"
        [void][IO.Directory]::CreateDirectory($parentTarget)
        [void](New-Item -ItemType Junction -Path $parentLink -Target $parentTarget)
        $junctions.Add($parentLink); $sentinels.Add($parentTarget)
        Assert-Throws { New-E2eManagedRunDirectory $repositoryRoot $parentLink ([guid]::NewGuid()) } 'parent junction rejection'

        $nestedGuid = [guid]::NewGuid()
        $nestedRun = New-E2eManagedRunDirectory $repositoryRoot $runsRoot $nestedGuid
        $createdRuns.Add([pscustomobject]@{ Guid = $nestedGuid; Path = $nestedRun })
        $outsideSentinel = Join-Path $repositoryRoot ".e2e-data\outside-sentinel-$([guid]::NewGuid().ToString('N'))"
        [void][IO.Directory]::CreateDirectory($outsideSentinel)
        [IO.File]::WriteAllText((Join-Path $outsideSentinel 'keep.txt'), 'must survive')
        $sentinels.Add($outsideSentinel)
        $nestedLink = Join-Path $nestedRun 'nested-junction'
        [void](New-Item -ItemType Junction -Path $nestedLink -Target $outsideSentinel)
        $junctions.Add($nestedLink)
        Assert-Throws { Assert-E2eNoReparsePath $repositoryRoot (Join-Path $nestedLink 'keep.txt') } 'nested junction rejection before write'
        Assert-Throws { Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $nestedGuid $nestedRun } 'cleanup refusal on nested reparse point'
        Assert-Equal ([IO.File]::ReadAllText((Join-Path $outsideSentinel 'keep.txt'))) 'must survive' 'outside-run data remains untouched'
        [IO.Directory]::Delete($nestedLink)
        [void]$junctions.Remove($nestedLink)
        Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $nestedGuid $nestedRun
        [void]$createdRuns.Remove(($createdRuns | Where-Object Path -eq $nestedRun | Select-Object -First 1))
    }

    $validJson = @'
{
  "name": "reawote-e2e",
  "services": {
    "database": {
      "volumes": [
        { "type": "volume", "source": "postgres_data", "target": "/var/lib/postgresql", "volume": {} }
      ]
    }
  },
  "volumes": {
    "postgres_data": { "name": "reawote-e2e-postgres-data" }
  }
}
'@ | ConvertFrom-Json
    Assert-Equal (Assert-E2eComposeDatabaseVolume $validJson 'reawote-e2e-postgres-data') 'postgres_data' 'PostgreSQL 18 mount target'
    Assert-Equal (Assert-E2eComposeDatabaseVolume $validJson 'reawote-e2e-postgres-data') 'postgres_data' 'logical Compose source resolves to engine volume name'

    $wrongTargetJson = '{"services":{"database":{"environment":{"POSTGRES_PASSWORD":"must-not-leak"},"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql/data"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"}}}' | ConvertFrom-Json
    Assert-ThrowsWithMessage {
        Assert-E2eComposeDatabaseVolume $wrongTargetJson 'reawote-e2e-postgres-data'
    } @("type='volume'", "source='postgres_data'", "target='/var/lib/postgresql/data'") 'Compose rejects wrong PostgreSQL target with safe mount details' @('must-not-leak')

    $twoMountsJson = '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"},{"type":"volume","source":"other_data","target":"/var/lib/postgresql/18/docker"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"},"other_data":{"name":"unexpected-volume"}}}' | ConvertFrom-Json
    Assert-ThrowsWithMessage {
        Assert-E2eComposeDatabaseVolume $twoMountsJson 'reawote-e2e-postgres-data'
    } @("source='postgres_data'", "target='/var/lib/postgresql'", "source='other_data'", "target='/var/lib/postgresql/18/docker'") 'Compose rejects two database mounts'

    $bindMountJson = '{"services":{"database":{"volumes":[{"type":"bind","source":"C:\\\\unsafe\\\\database","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"}}}' | ConvertFrom-Json
    Assert-ThrowsWithMessage {
        Assert-E2eComposeDatabaseVolume $bindMountJson 'reawote-e2e-postgres-data'
    } @("type='bind'", "source='<redacted-non-volume-source>'", "target='/var/lib/postgresql'") 'Compose rejects bind mount instead of named volume' @([string]$bindMountJson.services.database.volumes[0].source)

    foreach ($case in @(
        @('wrong engine volume name', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"foreign-volume"}}}'),
        @('anonymous volume', '{"services":{"database":{"volumes":[{"type":"volume","target":"/var/lib/postgresql"}]}},"volumes":{}}'),
        @('missing top-level volume definition', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{}}'),
        @('external volume', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data","external":true}}}'),
        @('tmpfs mount', '{"services":{"database":{"volumes":[{"type":"tmpfs","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data"}}}'),
        @('volume driver bind options', '{"services":{"database":{"volumes":[{"type":"volume","source":"postgres_data","target":"/var/lib/postgresql"}]}},"volumes":{"postgres_data":{"name":"reawote-e2e-postgres-data","driver":"local","driver_opts":{"type":"none","o":"bind","device":"C:\\\\unsafe"}}}}')
    )) {
        $name, $json = $case
        Assert-Throws { Assert-E2eComposeDatabaseVolume ($json | ConvertFrom-Json) 'reawote-e2e-postgres-data' } "Compose rejects $name"
    }

    $validRuntimeMount = @('{"Type":"volume","Name":"reawote-e2e-postgres-data","Source":"/var/lib/docker/volumes/reawote-e2e-postgres-data/_data","Destination":"/var/lib/postgresql"}' | ConvertFrom-Json)
    Assert-Equal (Assert-E2eRuntimeDatabaseVolume $validRuntimeMount 'reawote-e2e-postgres-data') 'reawote-e2e-postgres-data' 'runtime PostgreSQL 18 named-volume mount'
    $extraRuntimeMount = @($validRuntimeMount[0], ('{"Type":"bind","Source":"C:\\\\unexpected","Destination":"/var/lib/postgresql/18/docker"}' | ConvertFrom-Json))
    Assert-ThrowsWithMessage {
        Assert-E2eRuntimeDatabaseVolume $extraRuntimeMount 'reawote-e2e-postgres-data'
    } @("source='reawote-e2e-postgres-data'", "target='/var/lib/postgresql'", "type='bind'", "source='<redacted-non-volume-source>'", "target='/var/lib/postgresql/18/docker'") 'runtime rejects additional data-directory mount' @([string]$extraRuntimeMount[1].Source)

    $volumeInspectNullOptions = '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"local","Options":null,"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"postgres_data"}}' | ConvertFrom-Json
    $volumeInspectEmptyOptions = '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"local","Options":{},"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"postgres_data"}}' | ConvertFrom-Json
    Assert-Equal (Assert-E2eEngineVolumeInspection $volumeInspectNullOptions 'reawote-e2e-postgres-data' 'reawote-e2e') 'reawote-e2e-postgres-data' 'engine volume accepts local scope with null Options'
    Assert-Equal (Assert-E2eEngineVolumeInspection $volumeInspectEmptyOptions 'reawote-e2e-postgres-data' 'reawote-e2e') 'reawote-e2e-postgres-data' 'engine volume accepts local scope with empty Options'

    foreach ($case in @(
        @('different driver', '{"Name":"reawote-e2e-postgres-data","Driver":"custom","Scope":"local","Options":null,"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"postgres_data"}}'),
        @('different scope', '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"global","Options":null,"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"postgres_data"}}'),
        @('missing labels', '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"local","Options":null,"Labels":null}'),
        @('spoofed labels', '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"local","Options":null,"Labels":{"com.docker.compose.project":"reawote","com.docker.compose.volume":"postgres_data"}}'),
        @('spoofed volume label', '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"local","Options":null,"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"foreign_data"}}'),
        @('wrong name', '{"Name":"foreign-volume","Driver":"local","Scope":"local","Options":null,"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"postgres_data"}}')
    )) {
        $name, $json = $case
        Assert-Throws {
            Assert-E2eEngineVolumeInspection ($json | ConvertFrom-Json) 'reawote-e2e-postgres-data' 'reawote-e2e'
        } "engine volume rejects $name"
    }

    foreach ($case in @(
        @('bind options', '{"type":"none","o":"bind","device":"C:\\\\sensitive\\database"}', 'sensitive'),
        @('NFS options', '{"type":"nfs","o":"addr=10.0.0.10,rw","device":":/private/export"}', '10.0.0.10'),
        @('CIFS options', '{"type":"cifs","o":"username=secret,password=hidden","device":"//nas/private"}', '//nas/private')
    )) {
        $name, $optionsJson, $forbiddenValue = $case
        $inspectionJson = '{"Name":"reawote-e2e-postgres-data","Driver":"local","Scope":"local","Options":' + $optionsJson + ',"Labels":{"com.docker.compose.project":"reawote-e2e","com.docker.compose.volume":"postgres_data"}}'
        Assert-ThrowsWithMessage {
            Assert-E2eEngineVolumeInspection ($inspectionJson | ConvertFrom-Json) 'reawote-e2e-postgres-data' 'reawote-e2e'
        } @('non-empty Options') "engine volume rejects $name without exposing values" @($forbiddenValue, 'device')
    }

    $upEvents = [Collections.Generic.List[string]]::new()
    Invoke-E2eGuardedAction -Validation { $upEvents.Add('volume validation') } -Action { $upEvents.Add('compose up') }
    Assert-Equal ($upEvents -join ',') 'volume validation,compose up' 'volume validation runs before compose up'

    $dropEvents = [Collections.Generic.List[string]]::new()
    Invoke-E2eGuardedAction -Validation { $dropEvents.Add('volume validation') } -Action { $dropEvents.Add('DROP SCHEMA') }
    Assert-Equal ($dropEvents -join ',') 'volume validation,DROP SCHEMA' 'volume validation runs immediately before DROP SCHEMA'

    $refusedEvents = [Collections.Generic.List[string]]::new()
    Assert-Throws {
        Invoke-E2eGuardedAction -Validation {
            $refusedEvents.Add('volume validation')
            throw 'unsafe synthetic volume'
        } -Action {
            $refusedEvents.Add('DROP SCHEMA')
        }
    } 'failed volume validation prevents destructive database command'
    Assert-Equal ($refusedEvents -join ',') 'volume validation' 'destructive database command was not invoked after failed validation'

    Assert-Equal (Test-E2eLocalDockerEndpoint 'npipe:////./pipe/docker_engine' $true) $true 'local Windows Docker named pipe'
    Assert-Equal (Test-E2eLocalDockerEndpoint 'unix:///var/run/docker.sock' $false) $true 'local Unix Docker socket'
    Assert-Equal (Test-E2eLocalDockerEndpoint 'tcp://127.0.0.1:2375' $true) $false 'Docker tcp endpoint rejection'
    Assert-Equal (Test-E2eLocalDockerEndpoint 'ssh://builder.example.invalid' $false) $false 'Docker ssh endpoint rejection'

    $runnerText = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'test-demo-e2e.ps1'))
    Assert-Equal ($runnerText -notmatch '(?i)docker\s+volume\s+rm') $true 'runner never invokes docker volume rm'
    Assert-Equal ($runnerText -notmatch '(?i)--volumes\b') $true 'runner never removes Compose volumes'
    $allComposeUpCalls = @([regex]::Matches($runnerText, "Invoke-E2eCompose\s+@\('up'"))
    $guardedComposeUpCalls = @([regex]::Matches(
        $runnerText,
        "(?s)Invoke-E2eGuardedAction\s+-Validation\s+\{\s*\[void\]\(Assert-E2eDatabaseEngineVolume(?:\s+-RequirePresent)?\)\s*\}\s+-Action\s+\{\s*Invoke-E2eCompose\s+@\('up'"
    ))
    Assert-Equal $allComposeUpCalls.Count 4 'runner separately starts and awaits database and applications'
    Assert-Equal $guardedComposeUpCalls.Count $allComposeUpCalls.Count 'every Compose up is protected by engine volume validation'
    $guardedDropPattern = "(?s)function\s+Reset-E2eDatabaseSchema\s*\{.*?Invoke-E2eGuardedAction\s+-Validation\s+\{\s*\[void\]\(Assert-E2eDatabaseEngineVolume\s+-RequirePresent\)\s*\}\s+-Action\s+\{.*?DROP SCHEMA public CASCADE"
    Assert-Equal ([regex]::IsMatch($runnerText, $guardedDropPattern)) $true 'DROP SCHEMA is inside a require-present engine volume guard'
    $guardedProvisionPattern = "(?s)Invoke-E2eGuardedAction\s+-Validation\s+\{\s*\[void\]\(Assert-E2eDatabaseEngineVolume\s+-RequirePresent\)\s*Assert-RuntimeDatabaseMount\s*\}\s+-Action\s+\{\s*Invoke-E2eComposeWithStandardInput.*?'exec',\s*'--no-TTY',\s*'backend',\s*'python',\s*'-m',\s*'app\.auth\.cli'"
    Assert-Equal ([regex]::IsMatch($runnerText, $guardedProvisionPattern)) $true 'administrator provisioning is stdin-only and immediately volume guarded'
    Assert-Equal ($runnerText -notmatch "(?i)'--password'") $true 'runner never places an authentication password in CLI arguments'
    Assert-Equal ($runnerText -notmatch '\$runFailure\.Exception|\$\(\$_\.Exception\.Message\)') $true 'runner final and cleanup failures never include raw exception messages'
    Assert-Equal ($runnerText -match "(?s)finally\s*\{.*?Invoke-E2eCleanupOperation -OperationId 'cleanup_environment' -Action \{ Clear-E2eCredentialEnvironment \}") $true 'outer runner finally clears credential environment even before Playwright starts'
    Assert-Equal ($runnerText -match 'Invoke-E2eWithCredentialCleanup -Action') $true 'Playwright executes inside the tested credential finally wrapper'
    $manifestDefinition = [regex]::Match(
        $runnerText,
        '(?s)\$manifest\s*=\s*\[ordered\]@\{(?<body>.*?)\}\s*\|\s*ConvertTo-Json'
    )
    Assert-Equal $manifestDefinition.Success $true 'runner manifest definition is structurally recognizable'
    Assert-Equal ($manifestDefinition.Groups['body'].Value -notmatch '(?i)auth|password|cookie|csrf') $true 'runner manifest contains no authentication credentials'

    $playwrightConfigText = [IO.File]::ReadAllText((Join-Path $repositoryRoot 'frontend\playwright.config.ts'))
    Assert-Equal ($playwrightConfigText -match 'reporter:\s*"\.\/e2e\/safe-reporter\.ts"') $true 'Playwright uses the sanitized reporter'
    Assert-Equal ($playwrightConfigText -match 'PLAYWRIGHT_NO_COPY_PROMPT\s*=\s*"1"') $true 'Playwright automatic live-page failure context is disabled'
    Assert-Equal ($playwrightConfigText -match 'screenshot:\s*"off"') $true 'Playwright screenshots are disabled for authenticated scenarios'
    Assert-Equal ($playwrightConfigText -match 'trace:\s*"off"') $true 'Playwright traces are disabled for authenticated scenarios'
    Assert-Equal ($playwrightConfigText -match 'video:\s*"off"') $true 'Playwright videos are disabled for authenticated scenarios'
    $runManifestText = [IO.File]::ReadAllText((Join-Path $repositoryRoot 'frontend\e2e\run-manifest.ts'))
    Assert-Equal ($runManifestText -match 'e2eOutputDirectory\s*=\s*path\.join\(path\.dirname\(runManifest\.materialsRoot\)') $true 'transient Playwright framework files stay in the always-cleaned run directory'
    Assert-Equal ($runManifestText -notmatch 'e2eOutputDirectory\s*=\s*path\.join\(artifactRunRoot') $true 'unsanitized Playwright framework files are never retained as diagnostics'

    $mutexName = "Local\ReawoteDemoE2E-helper-$([guid]::NewGuid().ToString('N'))"
    $firstMutex = Enter-E2eRunMutex $mutexName
    try {
        $helperPath = Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1'
        $job = Start-Job -ScriptBlock {
            param($Path, $Name)
            . $Path
            try {
                $candidate = Enter-E2eRunMutex $Name
                Exit-E2eRunMutex $candidate
                return 'ACQUIRED'
            }
            catch { return 'REFUSED' }
        } -ArgumentList $helperPath, $mutexName
        $mutexResult = Receive-Job -Job $job -Wait
        Remove-Job -Job $job -Force
        Assert-Equal ($mutexResult -join '') 'REFUSED' 'second concurrent run mutex'
    }
    finally { Exit-E2eRunMutex $firstMutex }
}
finally {
    foreach ($junction in @($junctions)) { if (Test-Path -LiteralPath $junction) { [IO.Directory]::Delete($junction) } }
    foreach ($run in @($createdRuns)) {
        if (Test-Path -LiteralPath $run.Path) { Remove-E2eManagedRunDirectory $repositoryRoot $runsRoot $run.Guid $run.Path }
    }
    foreach ($sentinel in @($sentinels)) {
        if (Test-Path -LiteralPath (Join-Path $sentinel 'keep.txt')) { Remove-Item -LiteralPath (Join-Path $sentinel 'keep.txt') -Force }
        if (Test-Path -LiteralPath $sentinel) { Remove-Item -LiteralPath $sentinel -Force }
    }
}

& (Join-Path $PSScriptRoot 'test-e2e-stdin-transport.ps1')
& (Join-Path $PSScriptRoot 'test-e2e-phase-diagnostics.ps1')
& (Join-Path $PSScriptRoot 'test-e2e-runner-flow.ps1')
& (Join-Path $PSScriptRoot 'test-e2e-docker-context.ps1')

Write-Host 'All demo E2E helper safety tests passed.'
