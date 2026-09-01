param(
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentFile,

    [string]$TaskName = "",
    [string]$ArgusEndpoint = "http://127.0.0.1:8787",
    [string]$TokenFile = "C:\ProgramData\ARGUS\secrets\argus.token",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Set-EnvValues {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Geo Analyzer environment file not found: $Path"
    }

    $result = New-Object System.Collections.Generic.List[string]
    $written = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $rawLine.Trim()
        $separator = $trimmed.IndexOf("=")
        if ($separator -gt 0) {
            $name = $trimmed.Substring(0, $separator).Trim()
            if ($Values.Contains($name)) {
                if (-not $written.ContainsKey($name)) {
                    $result.Add("$name=$($Values[$name])")
                    $written[$name] = $true
                }
                continue
            }
        }
        $result.Add($rawLine)
    }
    foreach ($name in $Values.Keys) {
        if (-not $written.ContainsKey([string]$name)) {
            $result.Add("$name=$($Values[$name])")
        }
    }
    [IO.File]::WriteAllLines($Path, $result.ToArray(), [Text.UTF8Encoding]::new($false))
}

$uri = [Uri]$ArgusEndpoint
if ($uri.Scheme -notin @("http", "https")) {
    throw "ARGUS endpoint must use http or https"
}
if ($uri.Host -notin @("127.0.0.1", "localhost", "::1")) {
    throw "Server-local Geo Analyzer consumers must use a loopback ARGUS endpoint"
}
if (-not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) {
    throw "ARGUS token file not found: $TokenFile"
}

$healthUrl = $ArgusEndpoint.TrimEnd("/") + "/v1/health"
$health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 10
if ($health.status -ne "ok") {
    throw "ARGUS is not healthy: $($health.status)"
}

$plan = [ordered]@{
    EnvironmentFile = $EnvironmentFile
    TaskName = $TaskName
    ARGUS_SERVICE_BASE_URL = $ArgusEndpoint.TrimEnd("/")
    ARGUS_SERVICE_TOKEN_FILE = $TokenFile
    ArgusStatus = $health.status
    Mode = if ($Apply) { "apply" } else { "plan-only" }
}
$plan | ConvertTo-Json | Write-Host

if (-not $Apply) {
    Write-Host "Consumer configuration validated. No Geo Analyzer settings were changed."
    exit 0
}

Set-EnvValues -Path $EnvironmentFile -Values ([ordered]@{
    ARGUS_SERVICE_BASE_URL = $ArgusEndpoint.TrimEnd("/")
    ARGUS_SERVICE_TOKEN_FILE = $TokenFile
})

if (-not [string]::IsNullOrWhiteSpace($TaskName)) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        throw "Geo Analyzer scheduled task not found: $TaskName"
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Geo Analyzer consumer configuration applied."
