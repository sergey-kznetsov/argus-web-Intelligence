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

function Test-ProcessChainContainsPath {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$PathPrefix,
        [string]$ExactPython = ""
    )

    $normalizedPrefix = [IO.Path]::GetFullPath($PathPrefix).TrimEnd('\') + '\'
    $normalizedExactPython = ""
    if (-not [string]::IsNullOrWhiteSpace($ExactPython)) {
        $normalizedExactPython = [IO.Path]::GetFullPath($ExactPython)
    }

    $currentId = $ProcessId
    for ($depth = 0; $depth -lt 8 -and $currentId -gt 0; $depth++) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $currentId" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            break
        }

        $executable = [string]$process.ExecutablePath
        $commandLine = [string]$process.CommandLine

        if (-not [string]::IsNullOrWhiteSpace($executable)) {
            try {
                $normalizedExecutable = [IO.Path]::GetFullPath($executable)
                if (
                    -not [string]::IsNullOrWhiteSpace($normalizedExactPython) -and
                    $normalizedExecutable.Equals(
                        $normalizedExactPython,
                        [StringComparison]::OrdinalIgnoreCase
                    )
                ) {
                    return $true
                }
                if ($normalizedExecutable.StartsWith(
                    $normalizedPrefix,
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                    return $true
                }
            }
            catch {
                # Fall through to command-line and parent-chain verification.
            }
        }

        if (-not [string]::IsNullOrWhiteSpace($commandLine)) {
            if ($commandLine.IndexOf(
                $normalizedPrefix,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0) {
                return $true
            }
        }

        $currentId = [int]$process.ParentProcessId
    }

    return $false
}

function Test-LocalSystemArgusRuntime {
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)][ValidateSet("api", "worker")][string]$RuntimeRole
    )

    $commandLine = [string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }

    $escapedRole = [regex]::Escape($RuntimeRole)
    $runtimePattern = "(?i)(?:^|\s)-m\s+argus\.runtime_entrypoint\s+$escapedRole(?:\s|$)"
    if ($commandLine -notmatch $runtimePattern) {
        return $false
    }

    # Stop-ScheduledTask can terminate the PowerShell parent while leaving the Python
    # child alive. In that orphan case the release path is no longer available through
    # the parent chain. The production tasks run as LocalSystem, so an exact ARGUS
    # runtime command owned by SID S-1-5-18 is still a fail-closed managed identity.
    $owner = Invoke-CimMethod -InputObject $Process -MethodName GetOwnerSid -ErrorAction SilentlyContinue
    return $null -ne $owner -and [string]$owner.Sid -eq "S-1-5-18"
}

function Ensure-ArgusRuntimePort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ReleasesRoot,
        [Parameter(Mandatory = $true)][string]$CurrentRelease,
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

    $commandLine = [string]$process.CommandLine
    if (
        [string]::IsNullOrWhiteSpace($commandLine) -or
        -not $commandLine.Contains("argus.runtime_entrypoint")
    ) {
        throw "ARGUS $RuntimeRole port $Port is occupied by an unmanaged process PID $ownerId; refusing to terminate it"
    }

    # CPython venvs on Windows can expose the base interpreter as ExecutablePath while
    # the venv launcher/release path is visible only in the command line or parent chain.
    # Never use ExecutablePath alone as the ownership proof.
    $ownedRuntime = Test-ProcessChainContainsPath `
        -ProcessId $ownerId `
        -PathPrefix $ReleasesRoot
    if (-not $ownedRuntime) {
        $ownedRuntime = Test-LocalSystemArgusRuntime `
            -Process $process `
            -RuntimeRole $RuntimeRole
    }
    if (-not $ownedRuntime) {
        throw "ARGUS $RuntimeRole port $Port is occupied by an unverifiable ARGUS process PID $ownerId; refusing unsafe cleanup"
    }

    $sameRelease = Test-ProcessChainContainsPath `
        -ProcessId $ownerId `
        -PathPrefix $CurrentRelease `
        -ExactPython $CurrentPython
    $ownerKind = if ($sameRelease) { "same-release" } else { "previous-or-orphaned-release" }
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
        -CurrentRelease $release `
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
        -CurrentRelease $release `
        -CurrentPython $python `
        -RuntimeRole "worker"
    $processExitCode = Invoke-ArgusRuntime -Arguments @(
        "-m", "argus.runtime_entrypoint", "worker",
        "--probe-host", "127.0.0.1", "--probe-port", [string]$probePort
    ) -LogFile $logFile
}

exit $processExitCode
