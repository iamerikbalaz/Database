$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'docker-local.ps1')
$serviceRoot = Split-Path -Parent $PSScriptRoot
$serviceRun = 'reawote-packaging-service-' + [guid]::NewGuid().ToString('N')
$serviceImage = $serviceRun + ':runtime'
$serviceWorker = Join-Path $serviceRoot 'worker'
$serviceSmoke = Join-Path $serviceWorker 'tests/packaging_service_smoke.py'
$serviceStarted = $false
function Assert-ServiceCommand {
    param([string] $Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE." }
}
try {
    [void](Assert-LocalDockerContext)
    $existing = @(& docker ps --all --quiet --filter "name=^/${serviceRun}$")
    Assert-ServiceCommand 'Service namespace collision check'
    $images = @(& docker image ls --quiet $serviceImage)
    Assert-ServiceCommand 'Service image collision check'
    if ($existing.Count -or $images.Count) { throw 'Fresh service namespace already exists.' }
    Write-Host "Isolated production service image: $serviceImage"
    & docker build --target runtime --tag $serviceImage --file (Join-Path $serviceWorker 'Dockerfile.packaging') $serviceWorker
    Assert-ServiceCommand 'Production service build'
    [void](Assert-LocalDockerContext)
    $serviceStarted = $true
    & docker run --rm --name $serviceRun --label "com.reawote.packaging-smoke=$serviceRun" `
        --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 65532:65532 `
        --memory 4g --cpus 2 --pids-limit 256 --tmpfs '/tmp:rw,noexec,nosuid,size=2g' `
        --mount "type=bind,source=$serviceSmoke,target=/smoke.py,readonly" `
        $serviceImage python -c "import runpy; runpy.run_path('/smoke.py')['main'](variant='multi-current', ordered=True)"
    Assert-ServiceCommand 'Actual service HTTP/restart/download smoke'
    Write-Host "Retained production service image: $serviceImage"
}
finally {
    if ($serviceStarted) {
        [void](Assert-LocalDockerContext)
        $remaining = @(& docker ps --all --quiet --filter "name=^/${serviceRun}$")
        Assert-ServiceCommand 'Service cleanup check'
        if ($remaining.Count) {
            if ($remaining.Count -ne 1) { throw 'Ambiguous service ownership; cleanup refused.' }
            $owner = (& docker inspect --format '{{ index .Config.Labels "com.reawote.packaging-smoke" }}' $remaining[0]) -join ''
            Assert-ServiceCommand 'Service cleanup ownership check'
            if ($owner -cne $serviceRun) { throw 'Service cleanup ownership mismatch.' }
            & docker rm --force $remaining[0]
            Assert-ServiceCommand 'Owned service cleanup'
        }
    }
}
