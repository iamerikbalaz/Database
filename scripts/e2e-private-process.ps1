# Windows PowerShell 5.1 lacks ProcessStartInfo.ArgumentList. Start only a fixed
# trusted Node broker, then send all target arguments and input through its pipe.
$script:E2ePrivateBrokerDirectory = $PSScriptRoot

function New-E2ePrivateProcessResult {
    param([bool] $Started, [bool] $Completed, $ExitCode, $ErrorCategory)
    return [pscustomobject][ordered]@{
        started = $Started
        completed = $Completed
        exit_code = $ExitCode
        error_category = $ErrorCategory
    }
}

function Stop-E2ePrivateBrokerProcess {
    param([Diagnostics.Process] $Process)
    try {
        if ($null -eq $Process -or $Process.HasExited) { return }
        # Cooperate first: the broker owns the actual target handle. This still
        # works when Windows denies taskkill's process-tree enumeration.
        try {
            $cancel = [Text.UTF8Encoding]::new($false).GetBytes("{`"event`":`"cancel`"}`n")
            $cancelWrite = $Process.StandardInput.BaseStream.WriteAsync($cancel, 0, $cancel.Length)
            if (-not $cancelWrite.Wait(1000)) { throw 'E2E_PRIVATE_CANCEL_TIMEOUT' }
            [void]$cancelWrite.GetAwaiter().GetResult()
            $cancelFlush = $Process.StandardInput.BaseStream.FlushAsync()
            if (-not $cancelFlush.Wait(1000)) { throw 'E2E_PRIVATE_CANCEL_TIMEOUT' }
            [void]$cancelFlush.GetAwaiter().GetResult()
            $Process.StandardInput.BaseStream.Close()
        }
        catch { } # Broken control pipes use the specific-PID fallback below.
        if ($Process.WaitForExit(11000)) { return }
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            $killer = [Diagnostics.Process]::new()
            try {
                $killer.StartInfo.FileName = Join-Path ([Environment]::GetFolderPath('System')) 'taskkill.exe'
                # Only this owned broker PID and its descendants are eligible.
                $killer.StartInfo.Arguments = '/PID ' + ([int]$Process.Id).ToString() + ' /T /F'
                $killer.StartInfo.UseShellExecute = $false
                $killer.StartInfo.CreateNoWindow = $true
                $killer.StartInfo.RedirectStandardOutput = $true
                $killer.StartInfo.RedirectStandardError = $true
                [void]$killer.Start()
                $discardOutput = $killer.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
                $discardError = $killer.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
                if (-not $killer.WaitForExit(5000)) { $killer.Kill() }
                if (-not $Process.HasExited) { $Process.Kill() }
                [void]$Process.WaitForExit(5000)
                [void]$discardOutput.Wait(1000)
                [void]$discardError.Wait(1000)
            }
            finally { $killer.Dispose() }
        }
        else {
            try { $Process.Kill($true) }
            catch { $Process.Kill() }
            [void]$Process.WaitForExit(5000)
        }
    }
    catch {
        try {
            if ($null -ne $Process -and -not $Process.HasExited) { $Process.Kill() }
            if ($null -ne $Process) { [void]$Process.WaitForExit(5000) }
        }
        catch { } # Never expose process/OS errors during cleanup.
    }
}

function Invoke-E2ePrivateProcess {
    param(
        [string] $FilePath,
        [AllowEmptyCollection()] [string[]] $Arguments = @(),
        [AllowEmptyString()] [string] $StandardInput = '',
        [int] $TimeoutMilliseconds = 60000,
        [string] $WorkingDirectory = '',
        [scriptblock] $OnStarted
    )

    $process = $request = $requestJson = $inputBytes = $outputBytes = $null
    $started = $false
    $targetStarted = $false
    $failureCategory = 'invalid_state'
    try {
        if ([string]::IsNullOrEmpty($FilePath) -or $TimeoutMilliseconds -lt 1 -or $TimeoutMilliseconds -gt 3600000) {
            return New-E2ePrivateProcessResult $false $false $null 'invalid_state'
        }
        if ([string]::IsNullOrEmpty($WorkingDirectory)) { $WorkingDirectory = (Get-Location).ProviderPath }
        $WorkingDirectory = [IO.Path]::GetFullPath($WorkingDirectory)
        if (-not [IO.Directory]::Exists($WorkingDirectory)) {
            return New-E2ePrivateProcessResult $false $false $null 'invalid_state'
        }
        $failureCategory = 'process_start'
        $node = Get-Command node -CommandType Application -ErrorAction Stop
        $process = [Diagnostics.Process]::new()
        $process.StartInfo.FileName = $node.Source
        $process.StartInfo.WorkingDirectory = $script:E2ePrivateBrokerDirectory
        # Constant argv only: target path/arguments are never command text.
        $process.StartInfo.Arguments = 'e2e-private-process.mjs'
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.CreateNoWindow = $true
        $process.StartInfo.RedirectStandardInput = $true
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        [void]$process.Start()
        $started = $true
        $clock = [Diagnostics.Stopwatch]::StartNew()
        # The broker owns the target timeout; this outer bound covers IPC,
        # broker startup and its bounded process-tree termination grace period.
        $budget = $TimeoutMilliseconds + 15000
        $discardError = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $outputBytes = [byte[]]::new(4096)
        $outputRead = $process.StandardOutput.BaseStream.ReadAsync($outputBytes, 0, $outputBytes.Length)
        $request = [ordered]@{
            file_path = $FilePath
            arguments = @($Arguments)
            standard_input = $StandardInput
            working_directory = $WorkingDirectory
            timeout_ms = $TimeoutMilliseconds
        }
        $requestJson = $request | ConvertTo-Json -Depth 4 -Compress
        # The envelope is one JSON line. Keep this private control pipe open so
        # cancellation can reach the broker; the target's own stdin is closed
        # independently after its exact byte payload has been delivered.
        $inputBytes = [Text.UTF8Encoding]::new($false).GetBytes($requestJson + "`n")
        if ($inputBytes.Length -gt 33554432) {
            return New-E2ePrivateProcessResult $false $false $null 'invalid_state'
        }
        $failureCategory = 'stdin_io'
        $write = $process.StandardInput.BaseStream.WriteAsync($inputBytes, 0, $inputBytes.Length)
        $remaining = [Math]::Max(1, $budget - [int]$clock.ElapsedMilliseconds)
        if (-not $write.Wait($remaining)) {
            return New-E2ePrivateProcessResult $false $false $null 'timeout'
        }
        [void]$write.GetAwaiter().GetResult()
        # The Windows pipe BaseStream can buffer a small envelope. Flush it
        # explicitly because this control channel intentionally stays open.
        $flush = $process.StandardInput.BaseStream.FlushAsync()
        $remaining = [Math]::Max(1, $budget - [int]$clock.ElapsedMilliseconds)
        if (-not $flush.Wait($remaining)) {
            return New-E2ePrivateProcessResult $false $false $null 'timeout'
        }
        [void]$flush.GetAwaiter().GetResult()
        $failureCategory = 'unknown_safe_failure'
        $outputCount = 0
        $resultOffset = 0
        $firstLineSeen = $false
        while ($true) {
            $remaining = [Math]::Max(1, $budget - [int]$clock.ElapsedMilliseconds)
            if (-not $outputRead.Wait($remaining)) {
                return New-E2ePrivateProcessResult $targetStarted $false $null 'timeout'
            }
            $readCount = $outputRead.GetAwaiter().GetResult()
            if ($readCount -eq 0) { break }
            $outputCount += $readCount
            if ($outputCount -ge $outputBytes.Length) {
                return New-E2ePrivateProcessResult $targetStarted $false $null 'unknown_safe_failure'
            }
            if (-not $firstLineSeen) {
                for ($index = 0; $index -lt $outputCount; $index++) {
                    if ($outputBytes[$index] -eq 10) {
                        $firstLineSeen = $true
                        $firstLine = [Text.Encoding]::UTF8.GetString($outputBytes, 0, $index)
                        if ($firstLine -ceq '{"event":"started"}') {
                            $targetStarted = $true
                            $resultOffset = $index + 1
                            if ($null -ne $OnStarted) {
                                # The callback is trusted runner control flow,
                                # but even accidental output must stay private.
                                $callbackOutput = @(& $OnStarted 2>&1 3>&1 4>&1 5>&1 6>&1)
                                if ($callbackOutput.Count -ne 0) {
                                    return New-E2ePrivateProcessResult $true $false $null 'unknown_safe_failure'
                                }
                            }
                        }
                        break
                    }
                }
            }
            $outputRead = $process.StandardOutput.BaseStream.ReadAsync($outputBytes, $outputCount, $outputBytes.Length - $outputCount)
        }
        $remaining = [Math]::Max(1, $budget - [int]$clock.ElapsedMilliseconds)
        if (-not $process.WaitForExit($remaining)) {
            return New-E2ePrivateProcessResult $targetStarted $false $null 'timeout'
        }
        if ($process.ExitCode -ne 0) {
            $category = if ($targetStarted) { 'unknown_safe_failure' } else { 'process_start' }
            return New-E2ePrivateProcessResult $targetStarted $false $null $category
        }
        $rawResult = [Text.Encoding]::UTF8.GetString($outputBytes, $resultOffset, $outputCount - $resultOffset) | ConvertFrom-Json -ErrorAction Stop
        $keys = @($rawResult.PSObject.Properties.Name | Sort-Object)
        if (($keys -join ',') -cne 'completed,error_category,exit_code,started' -or
            $rawResult.started -isnot [bool] -or $rawResult.completed -isnot [bool] -or
            $rawResult.started -ne $targetStarted -or
            ($null -ne $rawResult.exit_code -and $rawResult.exit_code -isnot [int] -and $rawResult.exit_code -isnot [long]) -or
            ($null -ne $rawResult.error_category -and (
                $rawResult.error_category -isnot [string] -or $rawResult.error_category -cnotin @(
                    'process_start', 'stdin_io', 'timeout', 'nonzero_exit', 'invalid_state', 'unknown_safe_failure'
                )
            ))) {
            return New-E2ePrivateProcessResult $false $false $null 'unknown_safe_failure'
        }
        # Reconstruct the allowlisted object; never return the parsed payload.
        return New-E2ePrivateProcessResult $rawResult.started $rawResult.completed $rawResult.exit_code $rawResult.error_category
    }
    catch {
        return New-E2ePrivateProcessResult $targetStarted $false $null $failureCategory
    }
    finally {
        if ($started) { Stop-E2ePrivateBrokerProcess -Process $process }
        if ($null -ne $inputBytes) { [Array]::Clear($inputBytes, 0, $inputBytes.Length) }
        if ($null -ne $outputBytes) { [Array]::Clear($outputBytes, 0, $outputBytes.Length) }
        $request = $requestJson = $StandardInput = $null
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Invoke-E2ePrivateBootstrap {
    param(
        [string] $FilePath,
        [AllowEmptyCollection()] [string[]] $Arguments = @(),
        [AllowEmptyString()] [string] $StandardInput = '',
        [int] $TimeoutMilliseconds = 60000,
        [string] $WorkingDirectory = '',
        [scriptblock] $OnStarted
    )
    return Invoke-E2ePrivateProcess -FilePath $FilePath -Arguments $Arguments `
        -StandardInput $StandardInput -TimeoutMilliseconds $TimeoutMilliseconds `
        -WorkingDirectory $WorkingDirectory -OnStarted $OnStarted
}
