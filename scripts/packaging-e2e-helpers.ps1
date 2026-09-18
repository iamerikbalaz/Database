# Pure guards used before starting or touching the packaging E2E service.
function Assert-E2ePackagingCompose {
    param($Configuration, [string] $MaterialsRoot, [string] $VolumeName)
    $service = $Configuration.services.packaging
    if ($service.user -cne '65532:65532' -or $service.read_only -ne $true -or
        @($service.cap_drop) -cnotcontains 'ALL' -or @($service.security_opt) -notcontains 'no-new-privileges:true') {
        throw 'Packaging E2E requires its non-root read-only confined runtime.'
    }
    $ports = Get-E2eObjectPropertyValue $service 'ports'
    if ($null -ne $ports -and @($ports).Count -gt 0) { throw 'Packaging E2E must not publish host ports.' }
    if (@($service.networks.PSObject.Properties).Count -ne 1 -or
        $null -eq $service.networks.PSObject.Properties['packaging_private'] -or $Configuration.networks.packaging_private.internal -ne $true) {
        throw 'Packaging E2E requires its isolated internal network.'
    }
    if (@($service.volumes).Count -ne 2) { throw 'Packaging E2E has unexpected mounts.' }
    $source = @($service.volumes | Where-Object { $_.target -eq '/e2e-materials' })
    $storage = @($service.volumes | Where-Object { $_.target -eq '/e2e-packaging' })
    if ($source.Count -ne 1 -or $source[0].type -ne 'bind' -or $source[0].read_only -ne $true -or
        -not [IO.Path]::GetFullPath($source[0].source).Equals($MaterialsRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Packaging E2E source must be the exact read-only synthetic run root.'
    }
    if ($storage.Count -ne 1 -or $storage[0].type -ne 'volume' -or $storage[0].source -cne 'packaging_data') {
        throw 'Packaging E2E storage must be its own named volume.'
    }
    $definition = $Configuration.volumes.packaging_data
    $external = Get-E2eObjectPropertyValue $definition 'external'
    $driver = Get-E2eObjectPropertyValue $definition 'driver'
    $options = Get-E2eObjectPropertyValue $definition 'driver_opts'
    if ($definition.name -cne $VolumeName -or $external -eq $true -or ($driver -and $driver -ne 'local') -or
        ($null -ne $options -and @($options.PSObject.Properties).Count -gt 0)) {
        throw 'Packaging E2E storage requires the exact fresh local volume without external drivers or bind options.'
    }
}

function Test-E2ePackagingRuntimeSource {
    param([string] $Source, [string] $MaterialsRoot)
    $expected = [IO.Path]::GetFullPath($MaterialsRoot)
    $windowsPaths = [IO.Path]::DirectorySeparatorChar -eq '\'
    $comparison = if ($windowsPaths) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
    if ([IO.Path]::IsPathFullyQualified($Source) -and
        [IO.Path]::GetFullPath($Source).Equals($expected, $comparison)) { return $true }
    # The runner already requires the local Docker Desktop Linux endpoint and
    # verifies this synthetic host root without reparse points. Desktop may expose
    # its exact drive mapping in inspect; compare the full expected path instead
    # of stripping arbitrary Linux prefixes or using suffix/containment matches.
    if ($windowsPaths -and $expected -match '^[A-Za-z]:\\') {
        $desktopPath = '/run/desktop/mnt/host/' + $expected.Substring(0, 1).ToLowerInvariant() + '/' + $expected.Substring(3).Replace('\', '/')
        return $Source.Equals($desktopPath, [StringComparison]::OrdinalIgnoreCase)
    }
    return $false
}

function Assert-E2ePackagingRuntime {
    param($Container, $Network, [string] $ProjectName, [string] $MaterialsRoot, [string] $VolumeName)
    if ($Container.Config.Labels.'com.docker.compose.project' -cne $ProjectName -or
        $Container.Config.Labels.'com.docker.compose.service' -cne 'packaging' -or
        $Container.Config.User -cne '65532:65532' -or $Container.HostConfig.ReadonlyRootfs -ne $true -or
        @($Container.HostConfig.CapDrop) -notcontains 'ALL' -or
        @($Container.HostConfig.SecurityOpt) -notcontains 'no-new-privileges:true') { throw 'Unexpected packaging runtime ownership or confinement.' }
    if (@($Container.Mounts).Count -ne 2) { throw 'Unexpected packaging runtime mounts.' }
    $storage = @($Container.Mounts | Where-Object { $_.Destination -eq '/e2e-packaging' })
    $source = @($Container.Mounts | Where-Object { $_.Destination -eq '/e2e-materials' })
    if ($storage.Count -ne 1 -or $storage[0].Type -ne 'volume' -or $storage[0].Name -cne $VolumeName -or $storage[0].RW -ne $true) {
        throw 'Packaging runtime storage is not the exact writable named volume of this run.'
    }
    if ($source.Count -ne 1 -or $source[0].Type -ne 'bind' -or $source[0].RW -ne $false) {
        throw 'Packaging runtime source is not one read-only bind.'
    }
    if (-not (Test-E2ePackagingRuntimeSource $source[0].Source $MaterialsRoot)) {
        throw "Packaging runtime source differs from the synthetic run root: '$($source[0].Source)' versus '$MaterialsRoot'."
    }
    if (@($Container.NetworkSettings.Networks.PSObject.Properties).Count -ne 1 -or
        $null -eq $Container.NetworkSettings.Networks.PSObject.Properties[$Network.Name] -or
        $Network.Internal -ne $true -or $Network.Labels.'com.docker.compose.project' -cne $ProjectName -or
        $Network.Labels.'com.docker.compose.network' -cne 'packaging_private') { throw 'Unexpected packaging runtime network.' }
    if (@($Container.HostConfig.PortBindings.PSObject.Properties).Count -gt 0) { throw 'Packaging runtime unexpectedly publishes ports.' }
}
