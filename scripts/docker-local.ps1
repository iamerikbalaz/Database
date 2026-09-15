# Shared precondition for scripts that can change Docker resources.
function Restore-ProcessEnvironment {
    param([Parameter(Mandatory)] [hashtable] $Previous)
    foreach ($name in $Previous.Keys) {
        if ($null -eq $Previous[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
        else { [Environment]::SetEnvironmentVariable($name, $Previous[$name], 'Process') }
    }
}

function Initialize-LocalDocker {
    if (Get-Command docker -ErrorAction SilentlyContinue) { return }
    foreach ($root in @($env:LOCALAPPDATA, $env:ProgramFiles)) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        foreach ($relative in @('Programs\DockerDesktop\resources\bin', 'Docker\Docker\resources\bin')) {
            $directory = Join-Path $root $relative
            if (Test-Path -LiteralPath (Join-Path $directory 'docker.exe') -PathType Leaf) {
                $env:PATH = "$directory$([IO.Path]::PathSeparator)$env:PATH"
                return
            }
        }
    }
    throw 'Docker CLI is required. Start local Docker Desktop in Linux containers mode.'
}

function Test-LocalDockerEndpoint {
    param([Parameter(Mandatory)] [string] $Endpoint, [Parameter(Mandatory)] [bool] $WindowsHost)
    if ($WindowsHost) { return $Endpoint -cmatch '^npipe:////\./pipe/[A-Za-z0-9._-]+$' }
    return $Endpoint -cmatch '^unix:///[^\r\n]+$'
}

function Assert-LocalDockerContext {
    Initialize-LocalDocker
    $windowsHost = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $dockerHost = [Environment]::GetEnvironmentVariable('DOCKER_HOST', 'Process')
    if (-not [string]::IsNullOrWhiteSpace($dockerHost) -and
        -not (Test-LocalDockerEndpoint $dockerHost $windowsHost)) {
        throw 'DOCKER_HOST must use a local named pipe/Unix socket; remote endpoints are forbidden.'
    }
    $contextName = (& docker context show) -join ''
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($contextName)) {
        throw 'Could not determine the active Docker context.'
    }
    $contextName = $contextName.Trim()
    $contextJson = (& docker context inspect $contextName) -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the active Docker context.' }
    $contexts = @($contextJson | ConvertFrom-Json -ErrorAction Stop)
    if ($contexts.Count -ne 1) { throw 'Docker returned an unexpected context inspection result.' }
    if (-not (Test-LocalDockerEndpoint ([string]$contexts[0].Endpoints.docker.Host) $windowsHost)) {
        throw 'Active Docker context is remote; only a local named pipe/Unix socket is allowed.'
    }
    $engine = (& docker info --format '{{.OSType}}') -join ''
    if ($LASTEXITCODE -ne 0 -or $engine.Trim() -cne 'linux') {
        throw 'A reachable local Linux Docker Engine is required.'
    }
    return $contextName
}
