param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("api", "worker")]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [string]$ReleaseRoot,

    [string]$ConfigFile = "C:\ProgramData\ARGUS\argus.env"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Import-EnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "ARGUS environment file not found: $Path"
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $separator = $line.IndexOf("=")
        if ($separator -le 0) {
            continue
        }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name -notmatch '^[A-Z][A-Z0-9_]{0,127}$') {
            throw "Invalid ARGUS environment variable name: $name"
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Ensure-ArgusRuntimePort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ReleasesRoot,
        [Parameter(Mandatory = $true)][string]$CurrentPython,
        [Parameter(Mandatory = $true)][string]$RuntimeRole
    )

    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) {
        return
    }

    $ownerIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($ownerIds.Count -ne 1) {
        throw "ARGUS $RuntimeRole port $Port has multiple listeners; refusing unsafe cleanup"
    }

    $ownerId = [int]$ownerIds[0]
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        throw "ARGUS $RuntimeRole port $Port is occupied by an unknown process PID $ownerId"
    }

    $executable = [string]$process.ExecutablePath
    $commandLine = [string]$process.CommandLine
    if ([string]::IsNullOrWhiteSpace($executable) -or [string]::IsNullOrWhiteSpace($commandLine)) {
        throw "ARGUS $RuntimeRole port $Port is occupied by an unverifiable process PID $ownerId"
    }

    $normalizedExecutable = [IO.Path]::GetFullPath($executable)
    $normalizedReleasesRoot = [IO.Path]::GetFullPath($ReleasesRoot).TrimEnd('\') + '\'
    $ownedRuntime = $normalizedExecutable.StartsWith(
        $normalizedReleasesRoot,
        [StringComparison]::OrdinalIgnoreCase
    ) -and $commandLine.Contains("argus.runtime_entrypoint")

    if (-not $ownedRuntime) {
        throw "ARGUS $RuntimeRole port $Port is occupied by unmanaged process PID $ownerId; refusing to terminate it"
    }

    $normalizedCurrentPython = [IO.Path]::GetFullPath($CurrentPython)
    $ownerKind = if ($normalizedExecutable.Equals(
        $normalizedCurrentPython,
        [StringComparison]::OrdinalIgnoreCase
    )) { "same-release" } else { "previous-release" }
    Write-Host "Stopping stale $ownerKind ARGUS $RuntimeRole runtime PID $ownerId on port $Port"

    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $taskkill /PID ([string]$ownerId) /T /F *> $null
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        $remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            return
        }
    } while ((Get-Date) -lt $deadline)

    throw "ARGUS $RuntimeRole port $Port remained occupied after stopping managed runtime PID $ownerId"
}

function Invoke-ArgusRuntime {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogFile
    )

    # Windows PowerShell 5.1 promotes native stderr records to PowerShell errors.
    # Uvicorn and the worker legitimately log startup/runtime information to stderr,
    # so ErrorActionPreference=Stop would terminate an otherwise healthy service.
    # Keep strict error handling for all PowerShell setup work, relax it only while
    # the native Python process owns the foreground, then propagate its real exit code.
    $previousPreference = $ErrorActionPreference
    $nativeExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        & $python @Arguments *>> $LogFile
        $nativeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($null -eq $nativeExitCode) {
        return 1
    }
    return [int]$nativeExitCode
}

$release = [IO.Path]::GetFullPath($ReleaseRoot)
$releasesRoot = Split-Path -Parent $release
$python = Join-Path $release ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "ARGUS Python runtime not found: $python"
}

Import-EnvFile -Path $ConfigFile

if ([string]::IsNullOrWhiteSpace($env:ARGUS_TOKEN_FILE)) {
    throw "ARGUS_TOKEN_FILE is not configured"
}
if (-not (Test-Path -LiteralPath $env:ARGUS_TOKEN_FILE -PathType Leaf)) {
    throw "ARGUS token file not found"
}
if ([string]::IsNullOrWhiteSpace($env:ARGUS_DATABASE_DSN_FILE)) {
    throw "ARGUS_DATABASE_DSN_FILE is not configured"
}
if (-not (Test-Path -LiteralPath $env:ARGUS_DATABASE_DSN_FILE -PathType Leaf)) {
    throw "ARGUS database DSN file not found"
}

$env:ARGUS_STORAGE_BACKEND = "postgresql"
$env:PYTHONUNBUFFERED = "1"

$logsRoot = if ($env:ARGUS_LOG_DIR) { $env:ARGUS_LOG_DIR } else { "C:\ProgramData\ARGUS\logs" }
New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null
$logFile = Join-Path $logsRoot "$Role.log"

if ($Role -eq "api") {
    $port = if ($env:ARGUS_PORT) { [int]$env:ARGUS_PORT } else { 8787 }
    $env:ARGUS_EXECUTION_ROLE = "api"
    Ensure-ArgusRuntimePort `
        -Port $port `
        -ReleasesRoot $releasesRoot `
        -CurrentPython $python `
        -RuntimeRole "api"
    $processExitCode = Invoke-ArgusRuntime -Arguments @(
        "-m", "argus.runtime_entrypoint", "api",
        "--host", "127.0.0.1", "--port", [string]$port
    ) -LogFile $logFile
}
else {
    $probePort = if ($env:ARGUS_WORKER_PROBE_PORT) { [int]$env:ARGUS_WORKER_PROBE_PORT } else { 8788 }
    $env:ARGUS_EXECUTION_ROLE = "worker"
    Ensure-ArgusRuntimePort `
        -Port $probePort `
        -ReleasesRoot $releasesRoot `
        -CurrentPython $python `
        -RuntimeRole "worker"
    $processExitCode = Invoke-ArgusRuntime -Arguments @(
        "-m", "argus.runtime_entrypoint", "worker",
        "--probe-host", "127.0.0.1", "--probe-port", [string]$probePort
    ) -LogFile $logFile
}

exit $processExitCode
