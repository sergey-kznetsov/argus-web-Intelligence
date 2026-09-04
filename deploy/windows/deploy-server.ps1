param(
    [string]$Repository = "sergey-kznetsov/argus-web-Intelligence",
    [string]$Ref = "main",
    [string]$InstallRoot = "C:\argus",
    [string]$DataRoot = "C:\ProgramData\ARGUS",
    [string]$ApiTaskName = "ARGUS-API",
    [string]$WorkerTaskName = "ARGUS-Worker",
    [int]$ApiPort = 8787,
    [int]$WorkerProbePort = 8788,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Apply mode requires an elevated PowerShell session."
    }
}

function Import-EnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Environment file not found: $Path"
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $separator = $line.IndexOf("=")
        if ($separator -le 0) { continue }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name -notmatch '^[A-Z][A-Z0-9_]{0,127}$') {
            throw "Invalid environment variable name in $Path"
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Get-GithubToken {
    param([Parameter(Mandatory = $true)][string]$TokenFile)

    if (-not [string]::IsNullOrWhiteSpace($env:ARGUS_GITHUB_TOKEN)) {
        return $env:ARGUS_GITHUB_TOKEN.Trim()
    }

    if (Test-Path -LiteralPath $TokenFile -PathType Leaf) {
        $value = (Get-Content -LiteralPath $TokenFile -Raw -Encoding UTF8).Trim()
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }

    return ""
}

function Github-Headers {
    param([string]$Token)

    $headers = @{
        "Accept" = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent" = "ARGUS-Server-Deployment"
    }
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $headers["Authorization"] = "Bearer $Token"
    }
    return $headers
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
        }
    }
    finally {
        Pop-Location
    }
}

function Write-ManagedEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values
    )

    $result = New-Object System.Collections.Generic.List[string]
    $written = @{}
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
            $line = $rawLine
            $trimmed = $line.Trim()
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
            $result.Add($line)
        }
    }
    foreach ($name in $Values.Keys) {
        if (-not $written.ContainsKey([string]$name)) {
            $result.Add("$name=$($Values[$name])")
        }
    }
    [IO.File]::WriteAllLines($Path, $result.ToArray(), [Text.UTF8Encoding]::new($false))
}

function Get-TaskExists {
    param([string]$TaskName)
    return $null -ne (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
}

function Stop-ArgusTasks {
    foreach ($name in @($ApiTaskName, $WorkerTaskName)) {
        if (Get-TaskExists -TaskName $name) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

function Register-ArgusTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][ValidateSet("api", "worker")][string]$Role,
        [Parameter(Mandatory = $true)][string]$Release
    )

    $runner = Join-Path $Release "deploy\windows\run-process.ps1"
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
        throw "ARGUS process runner not found: $runner"
    }

    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -Role $Role -ReleaseRoot `"$Release`" -ConfigFile `"$ConfigFile`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Force | Out-Null
}

function Start-ArgusTasks {
    Start-ScheduledTask -TaskName $WorkerTaskName
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $ApiTaskName
}

function Test-ProcessBelongsToRelease {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Release
    )

    $normalizedRelease = [IO.Path]::GetFullPath($Release).TrimEnd('\') + '\'
    $expectedPython = [IO.Path]::GetFullPath((Join-Path $Release ".venv\Scripts\python.exe"))
    $currentId = $ProcessId

    for ($depth = 0; $depth -lt 8 -and $currentId -gt 0; $depth++) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $currentId" -ErrorAction SilentlyContinue
        if ($null -eq $process) { break }

        $executable = [string]$process.ExecutablePath
        $commandLine = [string]$process.CommandLine

        if (-not [string]::IsNullOrWhiteSpace($executable)) {
            try {
                $normalizedExecutable = [IO.Path]::GetFullPath($executable)
                if ($normalizedExecutable.Equals($expectedPython, [StringComparison]::OrdinalIgnoreCase)) {
                    return $true
                }
            }
            catch {
                # Continue with command-line and parent-chain verification.
            }
        }

        if (-not [string]::IsNullOrWhiteSpace($commandLine)) {
            if (
                $commandLine.Contains("run-process.ps1") -and
                $commandLine.IndexOf($normalizedRelease, [StringComparison]::OrdinalIgnoreCase) -ge 0
            ) {
                return $true
            }
        }

        $currentId = [int]$process.ParentProcessId
    }

    return $false
}

function Assert-ArgusListenersOwnedByRelease {
    param([Parameter(Mandatory = $true)][string]$Release)

    foreach ($port in @($ApiPort, $WorkerProbePort)) {
        $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -eq 0) {
            throw "ARGUS expected listener is missing on port $port"
        }

        $ownerIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($ownerIds.Count -ne 1) {
            throw "ARGUS port $port has multiple listener owners"
        }

        $ownerId = [int]$ownerIds[0]
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            throw "ARGUS port $port listener PID $ownerId could not be verified"
        }

        $commandLine = [string]$process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine) -or -not $commandLine.Contains("argus.runtime_entrypoint")) {
            throw "ARGUS port $port is not served by an ARGUS runtime entrypoint"
        }

        if (-not (Test-ProcessBelongsToRelease -ProcessId $ownerId -Release $Release)) {
            throw "ARGUS port $port is served by a different release PID $ownerId"
        }
    }
}

function Wait-ArgusHealth {
    param(
        [int]$TimeoutSeconds = 120,
        [Parameter(Mandatory = $true)][string]$ExpectedRelease
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $api = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/v1/health" -TimeoutSec 5
            $worker = Invoke-RestMethod -Uri "http://127.0.0.1:$WorkerProbePort/readyz" -TimeoutSec 5
            if ($api.status -eq "ok" -and $worker.status -eq "ok") {
                Assert-ArgusListenersOwnedByRelease -Release $ExpectedRelease
                return $api
            }
        }
        catch {
            # Startup is asynchronous; retry within the bounded deadline.
        }
        Start-Sleep -Seconds 2
    }

    throw "ARGUS did not become healthy within $TimeoutSeconds seconds"
}

function Show-ArgusLogs {
    foreach ($name in @("api.log", "worker.log")) {
        $path = Join-Path $LogsRoot $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Host "=== $name ==="
            Get-Content -LiteralPath $path -Tail 80
        }
    }
}

if ($ApiPort -lt 1024 -or $ApiPort -gt 65535) { throw "Invalid API port" }
if ($WorkerProbePort -lt 1024 -or $WorkerProbePort -gt 65535) { throw "Invalid worker probe port" }
if ($ApiPort -eq $WorkerProbePort) { throw "ARGUS API and worker probe ports must differ" }
if ($Ref -notmatch '^[0-9a-f]{40}$') { throw "ABORT: Ref must be an immutable 40-character commit SHA." }

$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$ReleasesRoot = Join-Path $InstallRoot "releases"
$SecretsRoot = Join-Path $DataRoot "secrets"
$LogsRoot = Join-Path $DataRoot "logs"
$ConfigFile = Join-Path $DataRoot "argus.env"
$StateFile = Join-Path $DataRoot "deployment.json"
$TokenFile = Join-Path $SecretsRoot "argus.token"
$DatabaseDsnFile = Join-Path $SecretsRoot "database-dsn.txt"
$GithubTokenFile = Join-Path $SecretsRoot "github-token.txt"

$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$freeDiskGiB = [math]::Round(([double]$disk.FreeSpace / 1GB), 2)
$databaseConfigured = (
    (Test-Path -LiteralPath $DatabaseDsnFile -PathType Leaf) -and
    -not [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $DatabaseDsnFile -Raw -Encoding UTF8).Trim())
)

$plan = [ordered]@{
    Repository = $Repository
    Ref = $Ref
    InstallRoot = $InstallRoot
    DataRoot = $DataRoot
    ConfigFile = $ConfigFile
    DatabaseDsnFile = $DatabaseDsnFile
    DatabaseConfigured = $databaseConfigured
    GithubTokenFile = $GithubTokenFile
    ApiTaskName = $ApiTaskName
    WorkerTaskName = $WorkerTaskName
    ApiEndpoint = "http://127.0.0.1:$ApiPort"
    WorkerProbe = "http://127.0.0.1:$WorkerProbePort/readyz"
    FreeDiskGiB = $freeDiskGiB
    Mode = if ($Apply) { "apply" } else { "plan-only" }
}
$plan | ConvertTo-Json | Write-Host

if (-not $Apply) {
    Write-Host "Plan validation succeeded. No server resources were changed."
    exit 0
}

Assert-Administrator
if ($freeDiskGiB -lt 5) {
    throw "ABORT: less than 5 GiB is free on C:."
}

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ReleasesRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
New-Item -ItemType Directory -Path $SecretsRoot -Force | Out-Null
New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null

if (-not (Test-Path -LiteralPath $DatabaseDsnFile -PathType Leaf)) {
    throw "ARGUS-owned PostgreSQL DSN file not found: $DatabaseDsnFile"
}
if ([string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $DatabaseDsnFile -Raw -Encoding UTF8).Trim())) {
    throw "ARGUS-owned PostgreSQL DSN file is empty: $DatabaseDsnFile"
}

$githubToken = Get-GithubToken -TokenFile $GithubTokenFile
$headers = Github-Headers -Token $githubToken
$metadata = Invoke-RestMethod `
    -Uri "https://api.github.com/repos/$Repository/commits/$Ref" `
    -Headers $headers `
    -TimeoutSec 30
$commit = [string]$metadata.sha
if ($commit -notmatch '^[0-9a-f]{40}$') {
    throw "GitHub returned an invalid commit SHA"
}
$releaseDir = Join-Path $ReleasesRoot $commit

$previousRelease = ""
if (Test-Path -LiteralPath $StateFile -PathType Leaf) {
    try {
        $state = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $previousRelease = [string]$state.current_release
    }
    catch {
        throw "Existing ARGUS deployment state is invalid"
    }
}

if (Test-Path -LiteralPath $releaseDir) {
    if ($releaseDir -ne $previousRelease) {
        Remove-Item -LiteralPath $releaseDir -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $releaseDir -PathType Container)) {
    $staging = Join-Path $InstallRoot (".staging-" + [Guid]::NewGuid().ToString("N"))
    $archive = Join-Path $staging "source.zip"
    $expanded = Join-Path $staging "expanded"
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    New-Item -ItemType Directory -Path $expanded -Force | Out-Null
    try {
        Invoke-WebRequest `
            -Uri "https://api.github.com/repos/$Repository/zipball/$commit" `
            -Headers $headers `
            -OutFile $archive `
            -TimeoutSec 120
        Expand-Archive -LiteralPath $archive -DestinationPath $expanded -Force
        $roots = @(Get-ChildItem -LiteralPath $expanded -Directory)
        if ($roots.Count -ne 1) {
            throw "Unexpected GitHub archive layout"
        }
        Move-Item -LiteralPath $roots[0].FullName -Destination $releaseDir
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

$python311 = "C:\Program Files\Python311\python.exe"
if (Test-Path -LiteralPath $python311 -PathType Leaf) {
    Invoke-Checked -FilePath $python311 -Arguments @("-m", "venv", (Join-Path $releaseDir ".venv")) -WorkingDirectory $releaseDir
}
else {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -eq $py) {
        throw "Python 3.11 was not found on the server"
    }
    Invoke-Checked -FilePath $py.Source -Arguments @("-3.11", "-m", "venv", (Join-Path $releaseDir ".venv")) -WorkingDirectory $releaseDir
}

$venvPython = Join-Path $releaseDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "ARGUS virtual environment was not created"
}
Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "install", "--disable-pip-version-check", ".") -WorkingDirectory $releaseDir
Invoke-Checked -FilePath $venvPython -Arguments @("-m", "playwright", "install", "chromium") -WorkingDirectory $releaseDir

$dbIdentityHelper = Join-Path $releaseDir "deploy\windows\read-dsn-identity.py"
if (-not (Test-Path -LiteralPath $dbIdentityHelper -PathType Leaf)) {
    throw "ARGUS PostgreSQL DSN identity helper not found: $dbIdentityHelper"
}
$dbIdentityOutput = @(& $venvPython $dbIdentityHelper $DatabaseDsnFile)
$dbIdentityExitCode = $LASTEXITCODE
$dbIdentityJson = ($dbIdentityOutput -join [Environment]::NewLine).Trim()
if ($dbIdentityExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($dbIdentityJson)) {
    throw "ARGUS PostgreSQL DSN identity could not be verified"
}
$dbIdentity = $dbIdentityJson | ConvertFrom-Json
if ([string]$dbIdentity.dbname -ne "argus") {
    throw "ABORT: standalone ARGUS must use PostgreSQL database 'argus'."
}
if ([string]$dbIdentity.user -ne "argus") {
    throw "ABORT: standalone ARGUS must use PostgreSQL service role 'argus'."
}

if (-not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) {
    $newToken = (& $venvPython -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
    if ([string]::IsNullOrWhiteSpace($newToken)) {
        throw "Failed to generate ARGUS bearer token"
    }
    [IO.File]::WriteAllText($TokenFile, $newToken + "`n", [Text.UTF8Encoding]::new($false))
    Remove-Variable newToken -ErrorAction SilentlyContinue
}

icacls.exe $SecretsRoot /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null

$managedEnv = [ordered]@{
    ARGUS_HOST = "127.0.0.1"
    ARGUS_PORT = [string]$ApiPort
    ARGUS_WORKER_PROBE_PORT = [string]$WorkerProbePort
    ARGUS_TOKEN_FILE = $TokenFile
    ARGUS_STORAGE_BACKEND = "postgresql"
    ARGUS_DATABASE_DSN_FILE = $DatabaseDsnFile
    ARGUS_LOG_DIR = $LogsRoot
    # Ollama is an acceleration/cognition dependency, not a process-liveness dependency.
    # Standalone ARGUS must still start and drain work through deterministic fallbacks if the
    # local model is temporarily unavailable or intentionally resource-throttled.
    ARGUS_LLM_REQUIRED = "false"
}
Write-ManagedEnv -Path $ConfigFile -Values $managedEnv
icacls.exe $ConfigFile /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null

Import-EnvFile -Path $ConfigFile
$env:ARGUS_EXECUTION_ROLE = "embedded"
Invoke-Checked -FilePath $venvPython -Arguments @("-m", "argus.runtime_entrypoint", "storage", "migrate") -WorkingDirectory $releaseDir
Invoke-Checked -FilePath $venvPython -Arguments @("-m", "argus.runtime_entrypoint", "storage", "check") -WorkingDirectory $releaseDir

Stop-ArgusTasks
Register-ArgusTask -TaskName $WorkerTaskName -Role "worker" -Release $releaseDir
Register-ArgusTask -TaskName $ApiTaskName -Role "api" -Release $releaseDir
Start-ArgusTasks

try {
    $health = Wait-ArgusHealth -TimeoutSeconds 120 -ExpectedRelease $releaseDir
}
catch {
    Show-ArgusLogs
    Stop-ArgusTasks
    if (-not [string]::IsNullOrWhiteSpace($previousRelease) -and (Test-Path -LiteralPath $previousRelease -PathType Container)) {
        Write-Host "New ARGUS release failed health-check. Rolling back to $previousRelease"
        Register-ArgusTask -TaskName $WorkerTaskName -Role "worker" -Release $previousRelease
        Register-ArgusTask -TaskName $ApiTaskName -Role "api" -Release $previousRelease
        Start-ArgusTasks
        Wait-ArgusHealth -TimeoutSeconds 120 -ExpectedRelease $previousRelease | Out-Null
    }
    throw
}

$statePayload = [ordered]@{
    schema_version = 1
    repository = $Repository
    commit = $commit
    current_release = $releaseDir
    previous_release = if ($previousRelease -and $previousRelease -ne $releaseDir) { $previousRelease } else { $null }
    api_endpoint = "http://127.0.0.1:$ApiPort"
    token_file = $TokenFile
    database_dsn_file = $DatabaseDsnFile
    deployed_at_utc = [DateTime]::UtcNow.ToString("o")
}
[IO.File]::WriteAllText(
    $StateFile,
    (($statePayload | ConvertTo-Json -Depth 5) + "`n"),
    [Text.UTF8Encoding]::new($false)
)

$previousCommit = ""
if (-not [string]::IsNullOrWhiteSpace($previousRelease)) {
    $previousCommit = Split-Path -Leaf $previousRelease
}
$deploymentChanged = $previousRelease -ne $releaseDir
try {
    $migrationResult = & $venvPython -m argus.storage.cli migrate --json | Out-String | ConvertFrom-Json
    $schemaCheck = & $venvPython -m argus.storage.cli check --json | Out-String | ConvertFrom-Json
}
catch {
    throw "ARGUS storage verification after cutover failed: $($_.Exception.Message)"
}
$deploymentVerification = [ordered]@{
    schema_version = 1
    verified_at_utc = [DateTime]::UtcNow.ToString("o")
    commit = $commit
    previous_commit = if ($previousCommit) { $previousCommit } else { $null }
    deployment_changed = $deploymentChanged
    api_status = [string]$health.status
    api_version = [string]$health.version
    database_backend = [string]$health.storage.backend
    storage_schema_version = [int]$schemaCheck.schema_version
    storage_expected_schema_version = [int]$schemaCheck.expected_schema_version
    storage_migrations_applied = @($migrationResult.applied)
}
$deploymentVerificationFile = Join-Path $DataRoot "deployment-verification.json"
[IO.File]::WriteAllText(
    $deploymentVerificationFile,
    (($deploymentVerification | ConvertTo-Json -Depth 5) + "`n"),
    [Text.UTF8Encoding]::new($false)
)

Write-Host "ARGUS deployment completed successfully."
Write-Host "Commit: $commit"
Write-Host "Current release: $releaseDir"
if ($previousRelease) { Write-Host "Previous release: $previousRelease" }
Write-Host "API: http://127.0.0.1:$ApiPort"
Write-Host "Worker readiness: http://127.0.0.1:$WorkerProbePort/readyz"
Write-Host "Storage database: argus"
Write-Host "Storage schema: argus"
Write-Host "Storage role: argus"
Write-Host "Storage schema version: $($schemaCheck.schema_version)"
Write-Host "Standalone LLM hard dependency: disabled"
Write-Host "Deployment verification: $deploymentVerificationFile"
