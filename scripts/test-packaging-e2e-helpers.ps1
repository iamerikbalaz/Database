$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'demo-e2e-helpers.ps1')
. (Join-Path $PSScriptRoot 'packaging-e2e-helpers.ps1')
$runSource = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $PSScriptRoot) '.e2e-data/runs/synthetic/materials'))
$runProject = 'reawote-e2e-synthetic'
$runVolume = $runProject + '-packaging-synthetic'
function New-Configuration {
    return (@{
        services = @{ packaging = @{ user = '65532:65532'; read_only = $true; cap_drop = @('ALL'); security_opt = @('no-new-privileges:true');
            networks = @{ packaging_private = @{} }; volumes = @(
                @{ type='bind'; source=$runSource; target='/e2e-materials'; read_only=$true },
                @{ type='volume'; source='packaging_data'; target='/e2e-packaging' }) } }
        volumes = @{ packaging_data = @{ name=$runVolume } }
        networks = @{ packaging_private = @{ internal=$true } }
    } | ConvertTo-Json -Depth 10 | ConvertFrom-Json)
}
function New-Runtime {
    return (@{
        Config=@{ User='65532:65532'; Labels=@{ 'com.docker.compose.project'=$runProject; 'com.docker.compose.service'='packaging' } }
        HostConfig=@{ ReadonlyRootfs=$true; CapDrop=@('ALL'); SecurityOpt=@('no-new-privileges:true'); PortBindings=@{} }
        Mounts=@(@{Type='bind';Source=$runSource;Destination='/e2e-materials';RW=$false},
            @{Type='volume';Name=$runVolume;Destination='/e2e-packaging';RW=$true})
        NetworkSettings=@{Networks=@{ ($runProject + '_packaging_private')=@{} }}
    } | ConvertTo-Json -Depth 10 | ConvertFrom-Json)
}
$runNetwork = @{ Name=$runProject+'_packaging_private'; Internal=$true; Labels=@{'com.docker.compose.project'=$runProject;'com.docker.compose.network'='packaging_private'} } | ConvertTo-Json | ConvertFrom-Json
Assert-E2ePackagingCompose (New-Configuration) $runSource $runVolume
Assert-E2ePackagingRuntime (New-Runtime) $runNetwork $runProject $runSource $runVolume
Write-Host 'PASS: exact isolated packaging compose and runtime accepted'
foreach ($kind in @('root','writable-source','wrong-source','foreign-volume','external-volume','driver-options','public-network','host-port','extra-mount')) {
    $value = New-Configuration
    switch ($kind) {
        root { $value.services.packaging.user='0:0' }
        writable-source { $value.services.packaging.volumes[0].read_only=$false }
        wrong-source { $value.services.packaging.volumes[0].source=Split-Path -Parent $runSource }
        foreign-volume { $value.volumes.packaging_data.name='foreign-volume' }
        external-volume { $value.volumes.packaging_data | Add-Member external $true }
        driver-options { $value.volumes.packaging_data | Add-Member driver_opts @{device='synthetic-foreign'} }
        public-network { $value.networks.packaging_private.internal=$false }
        host-port { $value.services.packaging | Add-Member ports @(@{published=8081}) }
        extra-mount { $value.services.packaging.volumes += $value.services.packaging.volumes[0] }
    }
    $rejected=$false
    try { Assert-E2ePackagingCompose $value $runSource $runVolume } catch { $rejected=$true }
    if (-not $rejected) { throw "Packaging compose guard accepted $kind" }
    Write-Host "PASS: packaging compose rejects $kind"
}
foreach ($kind in @('foreign-project','writable-source','foreign-volume','root','host-port','missing-confinement')) {
    $value=New-Runtime
    switch ($kind) {
        foreign-project { $value.Config.Labels.'com.docker.compose.project'='foreign-project' }
        writable-source { $value.Mounts[0].RW=$true }
        foreign-volume { $value.Mounts[1].Name='foreign-volume' }
        root { $value.Config.User='0:0' }
        host-port { $value.HostConfig.PortBindings | Add-Member '8081/tcp' @(@{HostPort=8081}) }
        missing-confinement { $value.HostConfig.CapDrop=@() }
    }
    $rejected=$false
    try { Assert-E2ePackagingRuntime $value $runNetwork $runProject $runSource $runVolume } catch { $rejected=$true }
    if (-not $rejected) { throw "Packaging runtime guard accepted $kind" }
    Write-Host "PASS: packaging runtime rejects $kind"
}
