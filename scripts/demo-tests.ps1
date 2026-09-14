$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "demo-common.ps1")

function Assert-Condition {
    param(
        [Parameter(Mandatory)] [bool] $Condition,
        [Parameter(Mandatory)] [string] $Label
    )
    if (-not $Condition) {
        throw "FAILED: $Label"
    }
    Write-Host "PASS: $Label"
}

function Assert-Throws {
    param(
        [Parameter(Mandatory)] [scriptblock] $Action,
        [Parameter(Mandatory)] [string] $Label
    )
    $threw = $false
    try {
        & $Action
    }
    catch {
        $threw = $true
    }
    Assert-Condition -Condition $threw -Label $Label
}

$context = Get-DemoContext
$testId = [guid]::NewGuid().ToString("N")
$demoRootExisted = Test-Path -LiteralPath $context.DemoDataRoot
$testRoot = Join-Path $context.DemoDataRoot "script-tests-$testId"
$safeNested = Join-Path $testRoot "safe/nested"
$safeFile = Join-Path $safeNested "fixture.txt"
$futureDeep = Join-Path $testRoot "future/deep"
$outsidePrefixRoot = Join-Path $context.RepositoryRoot ".demo-data-escape-$testId"
$outsidePrefixFile = Join-Path $outsidePrefixRoot "must-not-exist.txt"
$traversalFile = Join-Path $testRoot "safe/../must-not-exist.txt"
$junction = Join-Path $testRoot "junction"
$outsideTarget = Join-Path ([System.IO.Path]::GetTempPath()) "reawote-demo-path-test-$testId"
$junctionCreated = $false

try {
    $originalLocation = Get-Location
    try {
        Set-Location -LiteralPath ([System.IO.Path]::GetTempPath())
        $contextFromOtherDirectory = Get-DemoContext
    }
    finally {
        Set-Location -LiteralPath $originalLocation
    }
    Assert-Condition `
        -Condition ($contextFromOtherDirectory.RepositoryRoot -eq $context.RepositoryRoot) `
        -Label "repository root is derived from the trusted script path, not current directory"

    [void](New-SafeDemoDirectory -Context $context -Path $safeNested)
    [void](Write-SafeDemoTextFile -Context $context -Path $safeFile -Content "safe")
    Assert-Condition `
        -Condition (Test-Path -LiteralPath $safeFile -PathType Leaf) `
        -Label "ordinary path inside .demo-data is writable"

    Assert-Throws `
        -Action { [void](Write-SafeDemoTextFile -Context $context -Path $outsidePrefixFile -Content "escape") } `
        -Label "common textual prefix outside .demo-data is rejected"
    Assert-Condition `
        -Condition (-not (Test-Path -LiteralPath $outsidePrefixFile)) `
        -Label "rejected prefix path wrote nothing outside .demo-data"

    Assert-Throws `
        -Action { [void](Write-SafeDemoTextFile -Context $context -Path $traversalFile -Content "escape") } `
        -Label "parent traversal is rejected"
    Assert-Condition `
        -Condition (-not (Test-Path -LiteralPath (Join-Path $testRoot "must-not-exist.txt"))) `
        -Label "rejected traversal wrote no file"

    [void](New-SafeDemoDirectory -Context $context -Path $futureDeep)
    Assert-Condition `
        -Condition (Test-Path -LiteralPath $futureDeep -PathType Container) `
        -Label "nonexistent safe subpath is created level by level"

    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    Assert-Condition `
        -Condition (Test-CanonicalPathWithinRoot -Root $tempRoot -Candidate $outsideTarget) `
        -Label "junction test target is confined to the system temp directory"
    [void][System.IO.Directory]::CreateDirectory($outsideTarget)
    try {
        [void](New-Item `
            -ItemType Junction `
            -Path $junction `
            -Target $outsideTarget `
            -ErrorAction Stop)
        $junctionCreated = $true
    }
    catch {
        if ($_.Exception.Message -notmatch '(?i)privilege|permission|permitted|denied|support|oprávně|povolen') {
            throw
        }
        Write-Host "SKIP: junction creation is not permitted on this host"
    }

    if ($junctionCreated) {
        Assert-Throws `
            -Action { [void](Assert-SafeDemoPath -Context $context -Path $junction) } `
            -Label "existing reparse point is rejected"
        $junctionEscape = Join-Path $junction "must-not-exist.txt"
        Assert-Throws `
            -Action { [void](Write-SafeDemoTextFile -Context $context -Path $junctionEscape -Content "escape") } `
            -Label "reparse point in a parent component is rejected"
        Assert-Condition `
            -Condition (-not (Test-Path -LiteralPath (Join-Path $outsideTarget "must-not-exist.txt"))) `
            -Label "rejected reparse path wrote nothing in its outside target"
    }

    Assert-Condition `
        -Condition ((ConvertTo-DemoPort -Value "18000" -Name "test") -eq 18000) `
        -Label "valid numeric port is accepted"
    $validatedUrls = Get-DemoUrls -Ports ([pscustomobject]@{
        Backend = 18000
        Frontend = 15173
        Worker = 18080
    })
    Assert-Condition `
        -Condition ($validatedUrls.Backend -eq "http://localhost:18000") `
        -Label "URLs are built from validated numeric ports on localhost"
    foreach ($invalidPort in @(
        "not-a-port",
        "0",
        "65536",
        "0.0.0.0:18000",
        "*:18000",
        "18000:8000",
        "http://localhost:18000",
        " 18000 "
    )) {
        Assert-Throws `
            -Action { [void](ConvertTo-DemoPort -Value $invalidPort -Name "test") } `
            -Label "invalid port '$invalidPort' is rejected"
    }
    $previousInjectedPort = [System.Environment]::GetEnvironmentVariable(
        "DEMO_BACKEND_PORT",
        "Process"
    )
    try {
        [System.Environment]::SetEnvironmentVariable(
            "DEMO_BACKEND_PORT",
            "0.0.0.0:18000",
            "Process"
        )
        Assert-Throws `
            -Action { [void](Get-DemoPortConfiguration -Context $context) } `
            -Label "host injection through DEMO_BACKEND_PORT is rejected"
    }
    finally {
        [System.Environment]::SetEnvironmentVariable(
            "DEMO_BACKEND_PORT",
            $previousInjectedPort,
            "Process"
        )
    }

    $ports = [pscustomobject]@{ Backend = 18000; Frontend = 15173; Worker = 18080 }
    $composeEnvironmentNames = @(
        "BACKEND_PORT",
        "FRONTEND_PORT",
        "WORKER_PORT",
        "CORS_ORIGINS",
        "AUTH_COOKIE_SECURE",
        "AUTH_ALLOW_INSECURE_COOKIE"
    )
    $outerComposeEnvironment = @{}
    foreach ($name in $composeEnvironmentNames) {
        $outerComposeEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
        [System.Environment]::SetEnvironmentVariable($name, "pre-demo-$name", "Process")
    }
    try {
        $previousComposeEnvironment = Set-DemoComposePortEnvironment -Ports $ports
        Assert-Condition `
            -Condition ($env:AUTH_COOKIE_SECURE -eq "false" -and $env:AUTH_ALLOW_INSECURE_COOKIE -eq "true") `
            -Label "demo Compose calls opt in to insecure cookies only for local HTTP"
        Assert-Condition `
            -Condition ($env:CORS_ORIGINS -eq "http://localhost:15173,http://127.0.0.1:15173") `
            -Label "demo Compose calls constrain CORS origins to the selected loopback frontend port"
        Restore-DemoComposePortEnvironment -Previous $previousComposeEnvironment
        Assert-Condition `
            -Condition ($env:AUTH_COOKIE_SECURE -eq "pre-demo-AUTH_COOKIE_SECURE" -and
                $env:AUTH_ALLOW_INSECURE_COOKIE -eq "pre-demo-AUTH_ALLOW_INSECURE_COOKIE" -and
                $env:CORS_ORIGINS -eq "pre-demo-CORS_ORIGINS") `
            -Label "demo Compose authentication environment is restored after each call"
    }
    finally {
        foreach ($name in $composeEnvironmentNames) {
            [System.Environment]::SetEnvironmentVariable($name, $outerComposeEnvironment[$name], "Process")
        }
    }
    $validRendered = @{
        name = $context.ProjectName
        services = @{
            database = @{}
            backend = @{
                environment = @{
                    APP_ENV = "demo"
                    CORS_ORIGINS = "http://localhost:15173,http://127.0.0.1:15173"
                    AUTH_COOKIE_SECURE = "false"
                    AUTH_ALLOW_INSECURE_COOKIE = "true"
                }
                ports = @(@{ host_ip = "127.0.0.1"; published = "18000"; target = 8000 })
            }
            frontend = @{ ports = @(@{ host_ip = "127.0.0.1"; published = "15173"; target = 5173 }) }
            worker = @{
                ports = @(@{ host_ip = "127.0.0.1"; published = "18080"; target = 8080 })
                volumes = @(@{
                    type = "bind"
                    source = $context.MaterialsRoot
                    target = "/demo-materials"
                    read_only = $true
                })
            }
        }
    }
    Assert-DemoRenderedCompose `
        -Json ($validRendered | ConvertTo-Json -Depth 10) `
        -Context $context `
        -Ports $ports
    Write-Host "PASS: rendered loopback-only Compose ports are accepted"

    $invalidRendered = $validRendered | ConvertTo-Json -Depth 10 | ConvertFrom-Json
    $invalidRendered.services.backend.ports[0].host_ip = "0.0.0.0"
    Assert-Throws `
        -Action {
            Assert-DemoRenderedCompose `
                -Json ($invalidRendered | ConvertTo-Json -Depth 10) `
                -Context $context `
                -Ports $ports
        } `
        -Label "rendered Compose port on 0.0.0.0 is rejected before startup"

    $invalidAuthRendered = $validRendered | ConvertTo-Json -Depth 10 | ConvertFrom-Json
    $invalidAuthRendered.services.backend.environment.AUTH_ALLOW_INSECURE_COOKIE = "false"
    Assert-Throws `
        -Action {
            Assert-DemoRenderedCompose `
                -Json ($invalidAuthRendered | ConvertTo-Json -Depth 10) `
                -Context $context `
                -Ports $ports
        } `
        -Label "rendered demo without the explicit loopback HTTP authentication exception is rejected"

    $authHelperText = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot "demo-auth.ps1"))
    Assert-Condition `
        -Condition ($authHelperText -match '["'']exec["''],\s*["'']backend["''],\s*["'']python["''],\s*["'']-m["''],\s*["'']app\.auth\.cli["'']') `
        -Label "demo auth helper invokes the official backend CLI in the exact demo project"
    Assert-Condition `
        -Condition ($authHelperText -notmatch "(?i)--password|-T|--no-TTY") `
        -Label "demo auth helper keeps password entry interactive and out of arguments"

    Write-Host "All demo script security tests passed."
}
finally {
    if (Test-Path -LiteralPath $junction) {
        $junctionItem = Get-Item -LiteralPath $junction -Force -ErrorAction Stop
        if (($junctionItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
            throw "Refusing to clean up a junction test path that is no longer a reparse point."
        }
        Remove-Item -LiteralPath $junction -Force
    }

    if (Test-Path -LiteralPath $safeFile -PathType Leaf) {
        [void](Assert-SafeDemoPath -Context $context -Path $safeFile)
        [System.IO.File]::Delete($safeFile)
    }
    foreach ($directory in @(
        $futureDeep,
        (Split-Path -Parent $futureDeep),
        $safeNested,
        (Split-Path -Parent $safeNested),
        $testRoot
    )) {
        if (Test-Path -LiteralPath $directory -PathType Container) {
            [void](Assert-SafeDemoPath -Context $context -Path $directory)
            [System.IO.Directory]::Delete($directory, $false)
        }
    }
    if (-not $demoRootExisted -and
        (Test-Path -LiteralPath $context.DemoDataRoot -PathType Container) -and
        @([System.IO.Directory]::EnumerateFileSystemEntries($context.DemoDataRoot)).Count -eq 0) {
        [void](Assert-SafeDemoPath -Context $context -Path $context.DemoDataRoot)
        [System.IO.Directory]::Delete($context.DemoDataRoot, $false)
    }

    if (Test-Path -LiteralPath $outsideTarget -PathType Container) {
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not (Test-CanonicalPathWithinRoot -Root $tempRoot -Candidate $outsideTarget)) {
            throw "Refusing to clean up a path outside the test temp root."
        }
        if (@([System.IO.Directory]::EnumerateFileSystemEntries($outsideTarget)).Count -ne 0) {
            throw "Refusing to clean up non-empty junction test target: $outsideTarget"
        }
        [System.IO.Directory]::Delete($outsideTarget, $false)
    }
}
