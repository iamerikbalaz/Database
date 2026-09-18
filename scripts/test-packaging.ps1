$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'docker-local.ps1')

# No Compose, database, host input mounts or ports. Build a complete source
# snapshot, require the actual converter, and run as an unprivileged UID.
$packagingRoot = Split-Path -Parent $PSScriptRoot
$packagingRun = 'reawote-packaging-' + [guid]::NewGuid().ToString('N')
$packagingImage = $packagingRun + ':test'
$packagingWorker = Join-Path $packagingRoot 'worker'
$packagingDockerfile = Join-Path $packagingWorker 'Dockerfile.packaging'
$packagingStarted = $false

function Assert-PackagingCommand {
    param([string] $Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}

try {
    [void](Assert-LocalDockerContext)
    $existing = @(& docker ps --all --quiet --filter "name=^/${packagingRun}$")
    Assert-PackagingCommand 'Owned container collision check'
    $images = @(& docker image ls --quiet $packagingImage)
    Assert-PackagingCommand 'Owned image collision check'
    if ($existing.Count -ne 0 -or $images.Count -ne 0) { throw 'Fresh packaging namespace already exists.' }
    Write-Host "Isolated packaging run: $packagingRun"
    & docker build --tag $packagingImage --file $packagingDockerfile $packagingWorker
    Assert-PackagingCommand 'Packaging image build'
    [void](Assert-LocalDockerContext)
    $packagingStarted = $true
    & docker run --rm --name $packagingRun --label "com.reawote.packaging-test=$packagingRun" `
        --network none --read-only --cap-drop ALL --security-opt no-new-privileges `
        --user 65532:65532 --memory 4g --cpus 2 --pids-limit 256 `
        --tmpfs '/tmp:rw,noexec,nosuid,size=2g' --env REQUIRE_PACKAGING_RUNTIME=1 `
        $packagingImage python -m pytest -p no:cacheprovider --basetemp /tmp/pytest --tb=short
    Assert-PackagingCommand 'Complete worker suite with required conversion runtime'
    Write-Host "Retained test image: $packagingImage"
}
finally {
    if ($packagingStarted) {
        [void](Assert-LocalDockerContext)
        $remaining = @(& docker ps --all --quiet --filter "name=^/${packagingRun}$")
        Assert-PackagingCommand 'Owned cleanup check'
        if ($remaining.Count -gt 0) {
            if ($remaining.Count -ne 1) { throw 'Ambiguous packaging container; cleanup refused.' }
            $owner = (& docker inspect --format '{{ index .Config.Labels "com.reawote.packaging-test" }}' $remaining[0]) -join ''
            Assert-PackagingCommand 'Owned cleanup label check'
            if ($owner -cne $packagingRun) { throw 'Packaging ownership mismatch; cleanup refused.' }
            & docker rm --force $remaining[0]
            Assert-PackagingCommand 'Owned container cleanup'
        }
    }
}
