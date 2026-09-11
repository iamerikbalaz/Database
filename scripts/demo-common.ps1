Set-StrictMode -Version Latest

$script:DemoProjectName = "reawote-demo"

function Get-DemoContext {
    $repositoryRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
    $baseCompose = Join-Path $repositoryRoot "docker-compose.yml"
    $demoCompose = Join-Path $repositoryRoot "docker-compose.demo.yml"
    $localEnv = Join-Path $repositoryRoot ".env.demo"
    $exampleEnv = Join-Path $repositoryRoot ".env.demo.example"
    $envFile = if (Test-Path -LiteralPath $localEnv -PathType Leaf) {
        $localEnv
    }
    else {
        $exampleEnv
    }

    foreach ($requiredFile in @($baseCompose, $demoCompose, $envFile)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Required demo file is missing: $requiredFile"
        }
    }

    $demoDataRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot ".demo-data"))
    $repositoryPrefix = $repositoryRoot.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $demoDataRoot.StartsWith(
        $repositoryPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Resolved demo data root is outside the repository: $demoDataRoot"
    }

    return [pscustomobject]@{
        ProjectName = $script:DemoProjectName
        RepositoryRoot = $repositoryRoot
        BaseCompose = $baseCompose
        DemoCompose = $demoCompose
        EnvFile = $envFile
        DemoDataRoot = $demoDataRoot
        MaterialsRoot = Join-Path $demoDataRoot "materials"
        StateFile = Join-Path $demoDataRoot "demo-records.json"
    }
}

function Assert-DockerAvailable {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI is required. Start Docker Desktop and ensure 'docker' is available in PATH."
    }
}

function Get-DemoEnvironmentValue {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Default
    )

    $processValue = [System.Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue.Trim()
    }

    $prefix = $Name + "="
    foreach ($line in [System.IO.File]::ReadAllLines($Context.EnvFile)) {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $value = $trimmed.Substring($prefix.Length).Trim()
            return $value.Trim('"').Trim("'")
        }
    }
    return $Default
}

function Get-DemoUrls {
    param([Parameter(Mandatory)] $Context)

    $frontendBinding = Get-DemoEnvironmentValue $Context "FRONTEND_PORT" "127.0.0.1:15173"
    $backendBinding = Get-DemoEnvironmentValue $Context "BACKEND_PORT" "127.0.0.1:18000"
    $workerPort = Get-DemoEnvironmentValue $Context "WORKER_PORT" "18080"
    if ($frontendBinding -notmatch '^127\.0\.0\.1:(?<port>[0-9]+)$') {
        throw "FRONTEND_PORT must use the loopback-only form 127.0.0.1:<port>."
    }
    $frontendPort = [int]$Matches.port
    if ($backendBinding -notmatch '^127\.0\.0\.1:(?<port>[0-9]+)$') {
        throw "BACKEND_PORT must use the loopback-only form 127.0.0.1:<port>."
    }
    $backendPort = [int]$Matches.port
    if ($workerPort -notmatch '^[0-9]+$') {
        throw "WORKER_PORT must be a numeric TCP port."
    }
    $workerPort = [int]$workerPort
    foreach ($port in @($frontendPort, $backendPort, $workerPort)) {
        if ($port -lt 1 -or $port -gt 65535) {
            throw "Demo TCP ports must be between 1 and 65535."
        }
    }
    return [pscustomobject]@{
        Frontend = "http://localhost:$frontendPort"
        Backend = "http://localhost:$backendPort"
        Worker = "http://localhost:$workerPort"
    }
}

function Invoke-DemoCompose {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step
    )

    & docker compose `
        --project-name $Context.ProjectName `
        --env-file $Context.EnvFile `
        -f $Context.BaseCompose `
        -f $Context.DemoCompose `
        @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Invoke-DemoBaseCompose {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step
    )

    & docker compose `
        --project-name $Context.ProjectName `
        --env-file $Context.EnvFile `
        -f $Context.BaseCompose `
        @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}
