Set-StrictMode -Version Latest

$script:DemoProjectName = "reawote-demo"
$script:DemoDatabaseVolumeName = "reawote-demo-postgres-data"
$script:DemoCommonScriptPath = $MyInvocation.MyCommand.Path

function Test-CanonicalPathWithinRoot {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $Candidate
    )

    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $candidatePath = [System.IO.Path]::GetFullPath($Candidate)
    if ($candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $rootPrefix = $rootPath + [System.IO.Path]::DirectorySeparatorChar
    return $candidatePath.StartsWith(
        $rootPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-ExistingPathComponentsNoReparse {
    param(
        [Parameter(Mandatory)] [string] $Root,
        [Parameter(Mandatory)] [string] $Target
    )

    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $targetPath = [System.IO.Path]::GetFullPath($Target)
    if (-not (Test-CanonicalPathWithinRoot -Root $rootPath -Candidate $targetPath)) {
        throw "Path is outside the trusted repository root: $targetPath"
    }

    $rootItem = Get-Item -LiteralPath $rootPath -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Trusted repository root must not be a reparse point: $rootPath"
    }
    $resolvedRoot = [System.IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $rootPath -ErrorAction Stop).ProviderPath
    ).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if (-not $resolvedRoot.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        $rootPath = $resolvedRoot
        if (-not (Test-CanonicalPathWithinRoot -Root $rootPath -Candidate $targetPath)) {
            throw "Path does not remain below the resolved repository root: $targetPath"
        }
    }

    if ($targetPath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return
    }

    $rootPrefix = $rootPath + [System.IO.Path]::DirectorySeparatorChar
    $relativePath = $targetPath.Substring($rootPrefix.Length)
    $components = @($relativePath -split '[\\/]' | Where-Object { $_ -ne "" })
    $current = $rootPath
    $missingParent = $false
    for ($index = 0; $index -lt $components.Count; $index++) {
        $current = Join-Path $current $components[$index]
        try {
            $exists = Test-Path -LiteralPath $current -ErrorAction Stop
        }
        catch {
            throw "Could not safely inspect path component '$current': $($_.Exception.Message)"
        }
        if (-not $exists) {
            $missingParent = $true
            continue
        }
        if ($missingParent) {
            throw "A child exists below a missing path component; refusing ambiguous path: $current"
        }

        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse points, symlinks, junctions and mount points are forbidden: $current"
        }
        $resolved = [System.IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $current -ErrorAction Stop).ProviderPath
        )
        if (-not (Test-CanonicalPathWithinRoot -Root $rootPath -Candidate $resolved)) {
            throw "Resolved path escaped the trusted repository root: $current"
        }
        if ($index -lt ($components.Count - 1) -and -not $item.PSIsContainer) {
            throw "A non-directory path component cannot contain demo data: $current"
        }
    }
}

function Get-DemoContext {
    if ([string]::IsNullOrWhiteSpace($script:DemoCommonScriptPath)) {
        throw "Cannot determine the trusted demo-common.ps1 path."
    }

    $commonItem = Get-Item -LiteralPath $script:DemoCommonScriptPath -Force -ErrorAction Stop
    if (($commonItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "demo-common.ps1 must not be a reparse point."
    }
    $commonPath = [System.IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $commonItem.FullName -ErrorAction Stop).ProviderPath
    )
    $repositoryCandidate = Split-Path -Parent (Split-Path -Parent $commonPath)
    $repositoryRoot = [System.IO.Path]::GetFullPath(
        (Resolve-Path -LiteralPath $repositoryCandidate -ErrorAction Stop).ProviderPath
    ).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    Assert-ExistingPathComponentsNoReparse -Root $repositoryRoot -Target $commonPath

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
        Assert-ExistingPathComponentsNoReparse -Root $repositoryRoot -Target $requiredFile
    }

    $demoDataRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot ".demo-data"))
    Assert-ExistingPathComponentsNoReparse -Root $repositoryRoot -Target $demoDataRoot

    return [pscustomobject]@{
        ProjectName = $script:DemoProjectName
        DatabaseVolumeName = $script:DemoDatabaseVolumeName
        RepositoryRoot = $repositoryRoot
        BaseCompose = $baseCompose
        DemoCompose = $demoCompose
        EnvFile = $envFile
        DemoDataRoot = $demoDataRoot
        MaterialsRoot = Join-Path $demoDataRoot "materials"
        StateFile = Join-Path $demoDataRoot "demo-records.json"
    }
}

function Assert-SafeDemoPath {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string] $Path
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.Contains([char]0)) {
        throw "Demo path must be a non-empty filesystem path."
    }
    if ($Path -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Parent traversal is forbidden in demo paths: $Path"
    }
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "Demo write paths must be absolute: $Path"
    }

    $targetPath = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-CanonicalPathWithinRoot -Root $Context.DemoDataRoot -Candidate $targetPath)) {
        throw "Demo write path is outside the exact .demo-data root: $targetPath"
    }
    Assert-ExistingPathComponentsNoReparse `
        -Root $Context.RepositoryRoot `
        -Target $targetPath

    if (Test-Path -LiteralPath $targetPath) {
        $resolved = [System.IO.Path]::GetFullPath(
            (Resolve-Path -LiteralPath $targetPath -ErrorAction Stop).ProviderPath
        )
        if (-not (Test-CanonicalPathWithinRoot -Root $Context.DemoDataRoot -Candidate $resolved)) {
            throw "Resolved demo path escaped .demo-data: $targetPath"
        }
    }
    return $targetPath
}

function New-SafeDemoDirectory {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string] $Path
    )

    $targetPath = Assert-SafeDemoPath -Context $Context -Path $Path
    $repositoryPrefix = $Context.RepositoryRoot.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    ) + [System.IO.Path]::DirectorySeparatorChar
    $relativePath = $targetPath.Substring($repositoryPrefix.Length)
    $components = @($relativePath -split '[\\/]' | Where-Object { $_ -ne "" })
    $current = $Context.RepositoryRoot

    foreach ($component in $components) {
        $current = Join-Path $current $component
        [void](Assert-SafeDemoPath -Context $Context -Path $current)
        if (-not (Test-Path -LiteralPath $current)) {
            [void](Assert-SafeDemoPath -Context $Context -Path $current)
            [void][System.IO.Directory]::CreateDirectory($current)
        }
        [void](Assert-SafeDemoPath -Context $Context -Path $current)
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "Expected a demo directory but found another item type: $current"
        }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Created demo directory became a reparse point: $current"
        }
    }
    return $targetPath
}

function Write-SafeDemoTextFile {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Content
    )

    $targetPath = Assert-SafeDemoPath -Context $Context -Path $Path
    $parentPath = Split-Path -Parent $targetPath
    if (-not (Test-Path -LiteralPath $parentPath -PathType Container)) {
        throw "Demo file parent directory does not exist: $parentPath"
    }
    [void](Assert-SafeDemoPath -Context $Context -Path $parentPath)
    [void](Assert-SafeDemoPath -Context $Context -Path $targetPath)
    [System.IO.File]::WriteAllText(
        $targetPath,
        $Content,
        [System.Text.UTF8Encoding]::new($false)
    )
    [void](Assert-SafeDemoPath -Context $Context -Path $targetPath)
    return $targetPath
}

function Assert-DockerAvailable {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker CLI is required. Start Docker Desktop and ensure 'docker' is available in PATH."
    }
}

function Get-DemoEnvironmentRawValue {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Default
    )

    $processValue = [System.Environment]::GetEnvironmentVariable($Name, "Process")
    if ($null -ne $processValue) {
        return $processValue
    }

    $escapedName = [System.Text.RegularExpressions.Regex]::Escape($Name)
    foreach ($line in [System.IO.File]::ReadAllLines($Context.EnvFile)) {
        if ($line -match "^(?i:$escapedName)=(.*)$") {
            return $Matches[1]
        }
    }
    return $Default
}

function ConvertTo-DemoPort {
    param(
        [Parameter(Mandatory)] [string] $Value,
        [Parameter(Mandatory)] [string] $Name
    )

    if ($Value -notmatch '^[0-9]+$') {
        throw "$Name must contain only an integer TCP port without whitespace, host, URL or other text."
    }
    try {
        $port = [uint64]::Parse($Value, [System.Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        throw "$Name is not a valid TCP port."
    }
    if ($port -lt 1 -or $port -gt 65535) {
        throw "$Name must be between 1 and 65535."
    }
    return [int]$port
}

function Get-DemoPortConfiguration {
    param([Parameter(Mandatory)] $Context)

    return [pscustomobject]@{
        Backend = ConvertTo-DemoPort `
            -Value (Get-DemoEnvironmentRawValue $Context "DEMO_BACKEND_PORT" "18000") `
            -Name "DEMO_BACKEND_PORT"
        Frontend = ConvertTo-DemoPort `
            -Value (Get-DemoEnvironmentRawValue $Context "DEMO_FRONTEND_PORT" "15173") `
            -Name "DEMO_FRONTEND_PORT"
        Worker = ConvertTo-DemoPort `
            -Value (Get-DemoEnvironmentRawValue $Context "DEMO_WORKER_PORT" "18080") `
            -Name "DEMO_WORKER_PORT"
    }
}

function Get-DemoUrls {
    param([Parameter(Mandatory)] $Ports)

    $urls = [pscustomobject]@{
        Frontend = "http://localhost:$($Ports.Frontend)"
        Backend = "http://localhost:$($Ports.Backend)"
        Worker = "http://localhost:$($Ports.Worker)"
    }
    foreach ($property in $urls.PSObject.Properties) {
        $uri = $null
        if (-not [System.Uri]::TryCreate(
            [string]$property.Value,
            [System.UriKind]::Absolute,
            [ref]$uri
        )) {
            throw "Could not construct a valid absolute URL for $($property.Name)."
        }
        if ($uri.Scheme -ne "http" -or $uri.Host -ne "localhost" -or $uri.IsDefaultPort) {
            throw "Demo URL for $($property.Name) must use http://localhost with an explicit validated port."
        }
    }
    return $urls
}

function Set-DemoComposePortEnvironment {
    param([Parameter(Mandatory)] $Ports)

    $previous = @{}
    foreach ($name in @("BACKEND_PORT", "FRONTEND_PORT", "WORKER_PORT")) {
        $previous[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
    }
    [System.Environment]::SetEnvironmentVariable(
        "BACKEND_PORT", "127.0.0.1:$($Ports.Backend)", "Process"
    )
    [System.Environment]::SetEnvironmentVariable(
        "FRONTEND_PORT", "127.0.0.1:$($Ports.Frontend)", "Process"
    )
    [System.Environment]::SetEnvironmentVariable(
        "WORKER_PORT", [string]$Ports.Worker, "Process"
    )
    return $previous
}

function Restore-DemoComposePortEnvironment {
    param([Parameter(Mandatory)] [hashtable] $Previous)

    foreach ($name in @("BACKEND_PORT", "FRONTEND_PORT", "WORKER_PORT")) {
        [System.Environment]::SetEnvironmentVariable($name, $Previous[$name], "Process")
    }
}

function Invoke-DemoCompose {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] $Ports,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step
    )

    $previous = Set-DemoComposePortEnvironment -Ports $Ports
    $exitCode = 1
    try {
        & docker compose `
            --project-name $Context.ProjectName `
            --env-file $Context.EnvFile `
            -f $Context.BaseCompose `
            -f $Context.DemoCompose `
            @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        Restore-DemoComposePortEnvironment -Previous $previous
    }
    if ($exitCode -ne 0) {
        throw "$Step failed with exit code $exitCode."
    }
}

function Invoke-DemoBaseCompose {
    param(
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] $Ports,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Step
    )

    $previous = Set-DemoComposePortEnvironment -Ports $Ports
    $exitCode = 1
    try {
        & docker compose `
            --project-name $Context.ProjectName `
            --env-file $Context.EnvFile `
            -f $Context.BaseCompose `
            @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        Restore-DemoComposePortEnvironment -Previous $previous
    }
    if ($exitCode -ne 0) {
        throw "$Step failed with exit code $exitCode."
    }
}

function Assert-DemoRenderedCompose {
    param(
        [Parameter(Mandatory)] [string] $Json,
        [Parameter(Mandatory)] $Context,
        [Parameter(Mandatory)] $Ports
    )

    try {
        $configuration = $Json | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Rendered Compose configuration is not valid JSON."
    }
    if ($configuration.name -ne $Context.ProjectName) {
        throw "Rendered Compose project name is not '$($Context.ProjectName)'."
    }

    foreach ($serviceProperty in $configuration.services.PSObject.Properties) {
        $portsProperty = $serviceProperty.Value.PSObject.Properties["ports"]
        if ($null -eq $portsProperty) {
            continue
        }
        foreach ($publishedPort in @($portsProperty.Value)) {
            if ($null -eq $publishedPort) {
                continue
            }
            if ($publishedPort.host_ip -ne "127.0.0.1") {
                throw "Service '$($serviceProperty.Name)' publishes a port on forbidden host_ip '$($publishedPort.host_ip)'."
            }
        }
    }

    $expected = @{
        backend = @{ Published = $Ports.Backend; Target = 8000 }
        frontend = @{ Published = $Ports.Frontend; Target = 5173 }
        worker = @{ Published = $Ports.Worker; Target = 8080 }
    }
    foreach ($serviceName in $expected.Keys) {
        $service = $configuration.services.$serviceName
        if ($null -eq $service) {
            throw "Rendered Compose configuration is missing service '$serviceName'."
        }
        $matchingPorts = @($service.ports | Where-Object {
            [int]$_.published -eq $expected[$serviceName].Published -and
            [int]$_.target -eq $expected[$serviceName].Target -and
            $_.host_ip -eq "127.0.0.1"
        })
        if ($matchingPorts.Count -ne 1) {
            throw "Service '$serviceName' does not have exactly one expected loopback-only published port."
        }
    }

    $workerVolumes = @($configuration.services.worker.volumes | Where-Object {
        $_.type -eq "bind" -and $_.target -eq "/demo-materials"
    })
    if ($workerVolumes.Count -ne 1 -or $workerVolumes[0].read_only -ne $true) {
        throw "Rendered worker configuration must contain one read-only /demo-materials bind mount."
    }
    $renderedMaterialsSource = [System.IO.Path]::GetFullPath($workerVolumes[0].source)
    [void](Assert-SafeDemoPath -Context $Context -Path $renderedMaterialsSource)
    if (-not $renderedMaterialsSource.Equals(
        [System.IO.Path]::GetFullPath($Context.MaterialsRoot),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Rendered worker bind source is not the trusted demo materials directory."
    }
}
