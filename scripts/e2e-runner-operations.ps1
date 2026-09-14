$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Start-E2eRunnerPhase {
    param([Parameter(Mandatory)] [string] $Phase, [Parameter(Mandatory)] [string] $OperationId)
    Start-E2eDiagnosticOperation -State $script:E2eDiagnostics -Phase $Phase -OperationId $OperationId
}

function Complete-E2eRunnerPhase {
    Complete-E2eDiagnosticPhase -State $script:E2eDiagnostics
}

function Set-E2eRunnerOperation {
    param([Parameter(Mandatory)] [string] $OperationId)
    Start-E2eDiagnosticOperation -State $script:E2eDiagnostics `
        -Phase $script:E2eDiagnostics.current_phase -OperationId $OperationId
}

function Resolve-E2eReadExecutable {
    param(
        [Parameter(Mandatory)] [string] $Executable,
        [Parameter(Mandatory)] [string] $OperationId
    )

    Set-E2eRunnerOperation -OperationId $OperationId
    try {
        $name = $Executable
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
            @('docker', 'node') -contains $name) {
            $name += '.exe'
        }
        if ([Management.Automation.WildcardPattern]::ContainsWildcardCharacters($name)) {
            throw 'E2E_READ_EXECUTABLE_INVALID'
        }
        # Application-only lookup excludes functions, aliases and scripts. Never
        # expose the returned path, PATH, environment or lookup exception.
        $applications = @(Microsoft.PowerShell.Core\Get-Command -Name $name -CommandType Application -ErrorAction Stop)
        if ($applications.Count -ne 1 -or $applications[0].Source -isnot [string] -or
            -not [IO.Path]::IsPathRooted($applications[0].Source) -or
            -not [IO.File]::Exists($applications[0].Source)) {
            throw 'E2E_READ_EXECUTABLE_INVALID'
        }
        return $applications[0].Source
    }
    catch {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'executable_not_found' `
            -ExitCode $null -ProcessStarted $false
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
}

function ConvertTo-E2eNativeReadArgument {
    param([AllowEmptyString()] [string] $Value)

    # Windows PowerShell 5.1 has no ProcessStartInfo.ArgumentList. Quote each
    # argv value independently, preserving quotes and trailing backslashes.
    # No shell interprets these arguments (including Docker Go templates).
    return '"' + ([regex]::Replace($Value, '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Invoke-E2eNativeReadProcess {
    param(
        [Parameter(Mandatory)] [string] $FilePath,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [AllowEmptyString()] [string[]] $Arguments
    )

    # This object is private transport data, not a public diagnostic. In
    # particular stdout must never be passed to the diagnostic serializer.
    $result = [pscustomobject]@{
        started = $false
        exit_code = $null
        system_error_code = $null
        stdout = [string[]]@()
        error_category = 'process_start'
    }
    $process = $null
    $stdoutTask = $stderrTask = $stdoutText = $null
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (-not [IO.Path]::IsPathRooted($FilePath) -or
            [IO.Path]::GetFullPath($FilePath) -cne $FilePath) { return $result }
        $process = [Diagnostics.Process]::new()
        $process.StartInfo.FileName = $FilePath
        $process.StartInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-E2eNativeReadArgument $_ }) -join ' ')
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
        $process.StartInfo.RedirectStandardInput = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $process.StartInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false, $true)
        $result.started = $process.Start()
        if (-not $result.started) { return $result }
        $result.error_category = 'invalid_output'
        # Drain both pipes concurrently so stderr cannot deadlock stdout. Raw
        # stderr is discarded as bytes and never becomes a PowerShell ErrorRecord.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $process.StandardInput.Close()
        $remaining = [Math]::Max(0, 30000 - [int]$timer.ElapsedMilliseconds)
        if (-not $process.WaitForExit($remaining)) {
            $result.error_category = 'timeout'
            return $result
        }
        # Read ExitCode from this exact Process handle, never LASTEXITCODE (which
        # may belong to a previous command or be shadowed in a PowerShell scope).
        $result.error_category = 'missing_exit_code'
        $result.exit_code = $process.ExitCode
        $result.error_category = 'invalid_output'
        $remaining = [Math]::Max(0, 30000 - [int]$timer.ElapsedMilliseconds)
        if (-not $stdoutTask.Wait($remaining)) {
            $result.error_category = 'timeout'
            return $result
        }
        $remaining = [Math]::Max(0, 30000 - [int]$timer.ElapsedMilliseconds)
        if (-not $stderrTask.Wait($remaining)) {
            $result.error_category = 'timeout'
            return $result
        }
        $stdoutText = $stdoutTask.GetAwaiter().GetResult()
        [void]$stderrTask.GetAwaiter().GetResult()
        $lines = [Collections.Generic.List[string]]::new()
        $reader = [IO.StringReader]::new($stdoutText)
        try {
            while ($null -ne ($line = $reader.ReadLine())) { $lines.Add($line) }
        }
        finally { $reader.Dispose() }
        $result.stdout = $lines.ToArray()
        $result.error_category = $null
        return $result
    }
    catch {
        # Only an actual Win32 native code may leave an exception; neither its
        # message, type, command line nor stdout/stderr is serialized or printed.
        $nativeException = $_.Exception
        while ($null -ne $nativeException) {
            if ($nativeException -is [ComponentModel.Win32Exception]) {
                $result.system_error_code = [int]$nativeException.NativeErrorCode
                break
            }
            $nativeException = $nativeException.InnerException
        }
        return $result
    }
    finally {
        if ($null -ne $process) {
            try {
                if ($result.started -and -not $process.HasExited) {
                    # Only this owned read-command process is eligible.
                    $process.Kill()
                    [void]$process.WaitForExit(1000)
                }
            }
            catch { } # Cleanup must never reveal OS/process error text.
            try { $process.Dispose() }
            catch { }
        }
        $timer.Stop()
        $stdoutText = $stdoutTask = $stderrTask = $null
    }
}

function Invoke-E2eReadCommand {
    param(
        [Parameter(Mandatory)] [string] $Executable,
        [Parameter(Mandatory)] [AllowEmptyCollection()] [AllowEmptyString()] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $OperationId,
        [string] $ResolvedExecutable
    )

    Set-E2eRunnerOperation -OperationId $OperationId
    $application = if ([string]::IsNullOrEmpty($ResolvedExecutable)) {
        Resolve-E2eReadExecutable -Executable $Executable -OperationId $OperationId
    }
    else { $ResolvedExecutable }
    try { $result = Invoke-E2eNativeReadProcess -FilePath $application -Arguments $Arguments }
    catch {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'process_start' `
            -ExitCode $null -ProcessStarted $null
        throw 'E2E_SAFE_OPERATION_FAILED'
    }

    $started = Get-E2eSafeDiagnosticProcessStarted (Get-E2eDiagnosticProperty $result 'started')
    $nativeExit = Get-E2eSafeDiagnosticExitCode (Get-E2eDiagnosticProperty $result 'exit_code')
    $systemCode = Get-E2eSafeDiagnosticExitCode (Get-E2eDiagnosticProperty $result 'system_error_code')
    $category = Get-E2eAllowedDiagnosticValue (Get-E2eDiagnosticProperty $result 'error_category') $script:E2eDiagnosticErrorCategories
    # A PowerShell function returning an array unwraps singleton/empty arrays.
    # Inspect the property directly so its required collection shape is retained.
    $output = $null
    if ($result -is [Collections.IDictionary] -and $result.Contains('stdout')) {
        $output = $result['stdout']
    }
    elseif ($result -is [pscustomobject]) {
        $outputProperty = $result.PSObject.Properties['stdout']
        if ($null -ne $outputProperty -and $outputProperty.MemberType -eq 'NoteProperty') {
            $output = $outputProperty.Value
        }
    }
    if ($started -ne $true) { $category = 'process_start' }
    elseif ($null -eq $category -and $null -eq $nativeExit) { $category = 'missing_exit_code' }
    elseif ($null -eq $category -and $nativeExit -ne 0) { $category = 'nonzero_exit' }
    elseif ($null -eq $category -and ($output -isnot [array] -or @($output | Where-Object { $_ -isnot [string] }).Count -gt 0)) {
        $category = 'invalid_output'
    }
    if ($null -ne $category) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory $category `
            -ExitCode $nativeExit -ProcessStarted $started -SystemErrorCode $systemCode
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
    $script:E2eDiagnostics.process_started = $true
    $script:E2eDiagnostics.exit_code = $nativeExit
    $script:E2eDiagnostics.system_error_code = $null
    # Preserve the native-command line-array contract used by volume/container
    # callers, instead of returning a single multi-line string.
    return $output
}

function Assert-E2ePrivateResult {
    param([Parameter(Mandatory)] $Result)
    $safe = ConvertTo-E2eSafeProcessResult -Result $Result
    if (-not $safe.started -or -not $safe.completed -or $safe.exit_code -ne 0 -or $null -ne $safe.error_category) {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory $safe.error_category `
            -ExitCode $safe.exit_code -ProcessStarted $safe.started
        throw 'E2E_SAFE_OPERATION_FAILED'
    }
}

function Invoke-E2eRunnerPrivateProcess {
    param(
        [Parameter(Mandatory)] [string] $FilePath,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $OperationId,
        [AllowEmptyString()] [string] $StandardInput = '',
        [string] $WorkingDirectory = (Get-Location).ProviderPath,
        [int] $TimeoutMilliseconds = 60000,
        [scriptblock] $OnStarted,
        [switch] $Bootstrap
    )
    Set-E2eRunnerOperation -OperationId $OperationId
    $parameters = @{
        FilePath = $FilePath; Arguments = $Arguments; StandardInput = $StandardInput
        WorkingDirectory = $WorkingDirectory; TimeoutMilliseconds = $TimeoutMilliseconds
    }
    if ($null -ne $OnStarted) { $parameters.OnStarted = $OnStarted }
    $result = if ($Bootstrap) { Invoke-E2ePrivateBootstrap @parameters } else { Invoke-E2ePrivateProcess @parameters }
    Assert-E2ePrivateResult -Result $result
}

function Invoke-E2eCleanupOperation {
    param([Parameter(Mandatory)] [string] $OperationId, [Parameter(Mandatory)] [scriptblock] $Action)
    Start-E2eDiagnosticOperation -State $script:E2eDiagnostics -Phase 'cleanup' -OperationId $OperationId
    try { & $Action }
    catch {
        Set-E2eDiagnosticFailure -State $script:E2eDiagnostics -ErrorCategory 'unknown_safe_failure' -ExitCode $null
        $script:E2eCleanupFailed = $true
    }
}
