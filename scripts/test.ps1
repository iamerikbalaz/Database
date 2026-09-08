$ErrorActionPreference = "Stop"

function Assert-LastCommandSucceeded {
    param([string]$Step)

    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

docker compose config --quiet
Assert-LastCommandSucceeded "Docker Compose configuration validation"

$runningServices = @(docker compose ps --status running --services)
Assert-LastCommandSucceeded "Docker Compose service-state check"
$databaseWasRunning = $runningServices -contains "database"
$databaseStartAttempted = $false

try {
    docker compose build
    Assert-LastCommandSucceeded "Docker Compose build"

    $databaseStartAttempted = $true
    docker compose up -d --wait database
    Assert-LastCommandSucceeded "PostgreSQL startup"

    docker compose run --rm --no-deps backend pytest --ignore=tests/test_materials_postgresql.py
    Assert-LastCommandSucceeded "Backend unit tests"

    docker compose run --rm --no-deps -e RUN_POSTGRES_TESTS=1 backend pytest tests/test_materials_postgresql.py
    Assert-LastCommandSucceeded "PostgreSQL integration tests"

    docker compose run --rm --no-deps worker pytest
    Assert-LastCommandSucceeded "Worker tests"

    docker compose run --rm --no-deps frontend npm run lint
    Assert-LastCommandSucceeded "Frontend lint"

    docker compose run --rm --no-deps frontend npm run build
    Assert-LastCommandSucceeded "Frontend build"

    docker compose run --rm --no-deps frontend npm test
    Assert-LastCommandSucceeded "Frontend tests"
}
finally {
    if ($databaseStartAttempted -and -not $databaseWasRunning) {
        docker compose stop database
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "PostgreSQL cleanup failed with exit code $LASTEXITCODE."
        }
    }
}
