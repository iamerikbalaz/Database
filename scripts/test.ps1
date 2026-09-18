param([switch] $PostgresqlOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'docker-local.ps1')

function Assert-LastCommandSucceeded {
    param([string] $Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

# Always allocate a fresh namespace. Never start, migrate or reset the user's DB.
$testProject = 'reawote-test-' + [guid]::NewGuid().ToString('N')
$testRoot = Split-Path -Parent $PSScriptRoot
$testCompose = Join-Path $testRoot 'docker-compose.yml'
$testEnvFile = Join-Path $testRoot '.env.example'
$testEnvironment = @{
    POSTGRES_DB = 'reawote_test'; POSTGRES_USER = 'reawote_test'
    POSTGRES_PASSWORD = [guid]::NewGuid().ToString('N')
    APP_ENV = 'test'; AUTH_COOKIE_SECURE = 'true'; AUTH_ALLOW_INSECURE_COOKIE = 'false'
    BACKEND_PORT = '8000'; FRONTEND_PORT = '5173'; CORS_ORIGINS = 'http://localhost:5173'
}
$previousEnvironment = @{}
$started = $false

function Invoke-TestCompose {
    param([string[]] $Arguments, [string] $Step)
    [void](Assert-LocalDockerContext)
    & docker compose --project-name $testProject --env-file $testEnvFile -f $testCompose @Arguments
    Assert-LastCommandSucceeded $Step
}

try {
    [void](Assert-LocalDockerContext)
    foreach ($name in $testEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $testEnvironment[$name], 'Process')
    }
    $existing = @(& docker ps --all --quiet --filter "label=com.docker.compose.project=$testProject")
    Assert-LastCommandSucceeded 'Test container ownership check'
    $volumes = @(& docker volume ls --quiet --filter "name=^${testProject}_postgres_data$")
    Assert-LastCommandSucceeded 'Test volume ownership check'
    if ($existing.Count -ne 0 -or $volumes.Count -ne 0) {
        throw 'Fresh test namespace already exists; refusing all mutations.'
    }
    Write-Host "Isolated test project: $testProject"
    Invoke-TestCompose @('config', '--quiet') 'Docker Compose configuration validation'
    if ($PostgresqlOnly) { Invoke-TestCompose @('build', 'backend') 'Backend test image build' }
    else { Invoke-TestCompose @('build') 'Docker Compose build' }
    $started = $true
    Invoke-TestCompose @('up', '-d', '--wait', 'database') 'PostgreSQL startup'
    if (-not $PostgresqlOnly) {
        Invoke-TestCompose @('run', '--rm', '--no-deps', 'backend', 'pytest', '--ignore=tests/test_materials_postgresql.py', '--ignore=tests/test_auth_postgresql.py', '--ignore=tests/test_packaging_dispatch_lease_postgresql.py') 'Backend unit tests'
    }
    Write-Host 'PostgreSQL integration tests: auth and materials (auth skips fail the run).'
    Invoke-TestCompose @('run', '--rm', '--no-deps', '-e', 'RUN_POSTGRES_TESTS=1', 'backend', 'pytest', '--require-auth-postgresql', 'tests/test_auth_postgresql.py', 'tests/test_materials_postgresql.py', 'tests/test_packaging_dispatch_lease_postgresql.py') 'PostgreSQL integration tests'
    if (-not $PostgresqlOnly) {
        Invoke-TestCompose @('run', '--rm', '--no-deps', 'worker', 'pytest') 'Worker tests'
        Invoke-TestCompose @('run', '--rm', '--no-deps', 'frontend', 'npm', 'run', 'lint') 'Frontend lint'
        Invoke-TestCompose @('run', '--rm', '--no-deps', 'frontend', 'npm', 'run', 'build') 'Frontend build'
        Invoke-TestCompose @('run', '--rm', '--no-deps', 'frontend', 'npm', 'test') 'Frontend tests'
    }
}
finally {
    try {
        if ($started) {
            Invoke-TestCompose @('down', '--remove-orphans') 'Owned test container cleanup'
            Write-Host "Retained isolated test volume: ${testProject}_postgres_data"
        }
    }
    finally {
        Restore-ProcessEnvironment $previousEnvironment
    }
}
