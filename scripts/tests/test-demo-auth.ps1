$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module Microsoft.PowerShell.Utility
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'demo-auth.ps1')
$script:Calls = @()
$script:LoginRole = 'ADMIN'
$script:Forced = $false
$script:Fail = $false
function Invoke-WebRequest {
    param($UseBasicParsing, $Method, $Uri, $WebSession, $TimeoutSec, $MaximumRedirection, $Headers, $ContentType, $Body)
    if ($script:Fail) { throw 'SYNTHETIC_SECRET_REFLECTION' }
    $script:Calls += @{ Method = $Method; Uri = $Uri; Cookies = $WebSession; Redirects = $MaximumRedirection; Headers = $Headers }
    if ($Uri.EndsWith('/auth/login')) {
        return @{ Content = (@{ user = @{ role = $script:LoginRole }; csrf_token = 'synthetic-csrf'; must_change_password = $script:Forced } | ConvertTo-Json) }
    }
    return @{ Content = '{}' }
}
function Assert-Test([bool] $Condition, [string] $Message) {
    if (-not $Condition) { throw "FAILED: $Message" }
    Write-Host "PASS: $Message"
}
$credential = [pscredential]::new('synthetic@example.invalid', (ConvertTo-SecureString 'Synthetic test secret' -AsPlainText -Force))
$session = New-DemoApiSession 'http://localhost:18000' 'http://localhost:15173' $credential
[void](Invoke-DemoSessionRequest $session POST '/api/companies' @{ name = 'Fixture' })
Assert-Test ($script:Calls.Count -eq 2 -and $script:Calls[1].Headers['X-CSRF-Token'] -eq 'synthetic-csrf') 'mutations send the authenticated CSRF token'
Assert-Test ($script:Calls[1].Headers.Origin -eq 'http://localhost:15173' -and $script:Calls[1].Redirects -eq 0) 'trusted origin and disabled redirects are enforced'
Assert-Test ($script:Calls[0].Cookies -eq $script:Calls[1].Cookies) 'requests share only the in-memory cookie session'
Close-DemoApiSession $session
Assert-Test ($script:Calls[-1].Uri.EndsWith('/auth/logout') -and $null -eq $session.Cookies -and $null -eq $session.Csrf) 'logout clears local authentication state'
foreach ($endpoint in @('https://external.invalid:18000', 'http://localhost:18000/redirect', 'http://localhost:18000?next=external', 'http://user@localhost:18000')) {
    $before = $script:Calls.Count; $rejected = $false
    try { [void](New-DemoApiSession $endpoint 'http://localhost:15173' $credential) } catch { $rejected = $true }
    Assert-Test ($rejected -and $script:Calls.Count -eq $before) 'unsafe credential destination rejected before HTTP'
}
foreach ($variant in @('forced', 'role')) {
    $script:Forced = $variant -eq 'forced'; $script:LoginRole = if ($variant -eq 'role') { 'PROCESSOR' } else { 'ADMIN' }
    $rejected = $false
    try { [void](New-DemoApiSession 'http://localhost:18000' 'http://localhost:15173' $credential) } catch { $rejected = $true }
    Assert-Test ($rejected -and $script:Calls[-1].Uri.EndsWith('/auth/logout')) 'unusable seed account is rejected and logged out'
}
$script:Fail = $true; $message = ''
try { [void](New-DemoApiSession 'http://localhost:18000' 'http://localhost:15173' $credential) } catch { $message = $_.Exception.Message }
Assert-Test ($message -like 'Demo API request failed*' -and $message -notlike '*SYNTHETIC_SECRET*') 'HTTP failure details cannot reflect credentials'
Write-Host 'All 11 demo authentication checks passed.'
