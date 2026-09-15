# Interactive demo authentication. Credentials and cookies stay in memory.
Set-StrictMode -Version Latest

function Invoke-DemoSessionRequest {
    param(
        [Parameter(Mandatory)] $Session,
        [Parameter(Mandatory)] [ValidateSet('GET', 'POST')] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [hashtable] $Body
    )
    if ($Path -notmatch '^/api/[a-z0-9/?=&%._-]+$') { throw 'Invalid demo API path.' }
    $parameters = @{
        UseBasicParsing = $true; Method = $Method; Uri = $Session.Backend + $Path
        WebSession = $Session.Cookies; TimeoutSec = 10; MaximumRedirection = 0
        Headers = @{ Accept = 'application/json'; Origin = $Session.Origin }
    }
    if ($Method -ne 'GET' -and $Session.Csrf) { $parameters.Headers['X-CSRF-Token'] = $Session.Csrf }
    if ($null -ne $Body) {
        $parameters.ContentType = 'application/json'
        $parameters.Body = $Body | ConvertTo-Json -Depth 10 -Compress
    }
    try { return (Invoke-WebRequest @parameters).Content | ConvertFrom-Json }
    catch { throw 'Demo API request failed. Check the account, completed password change and demo service status.' }
    finally { [void]$parameters.Remove('Body'); $Body = $null }
}

function New-DemoApiSession {
    param(
        [Parameter(Mandatory)] [string] $Backend,
        [Parameter(Mandatory)] [string] $Origin,
        [Parameter(Mandatory)] [pscredential] $Credential
    )
    foreach ($endpoint in @($Backend, $Origin)) {
        $uri = [uri]$endpoint
        if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'http' -or $uri.Host -ne 'localhost' -or
            $uri.IsDefaultPort -or $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
            throw 'Demo credentials may only be sent to the validated localhost demo endpoints.'
        }
    }
    $session = [pscustomobject]@{
        Backend = $Backend; Origin = $Origin; Csrf = $null
        Cookies = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    }
    try {
        $login = Invoke-DemoSessionRequest $session POST '/api/auth/login' @{
            email = $Credential.UserName; password = $Credential.GetNetworkCredential().Password
        }
        $session.Csrf = $login.csrf_token
        if ($login.must_change_password -or $login.user.role -ne 'ADMIN') {
            Close-DemoApiSession $session
            throw 'Sign in through the browser as an administrator and complete the password change before seeding.'
        }
        return $session
    } finally { $Credential = $null; $login = $null }
}

function Close-DemoApiSession {
    param([Parameter(Mandatory)] $Session)
    try { [void](Invoke-DemoSessionRequest $Session POST '/api/auth/logout') }
    catch { Write-Warning 'Demo seed session logout failed. The session will expire normally.' }
    finally { $Session.Csrf = $null; $Session.Cookies = $null }
}
