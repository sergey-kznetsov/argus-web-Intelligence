param(
    [string]$DataRoot = "C:\ProgramData\ARGUS",
    [string]$BaseModel = "qwen3:8b",
    [string]$TunedModel = "argus-qwen3:8b-cpu",
    [int]$NumThread = 0,
    [int]$NumCtx = 4096,
    [int]$NumPredict = 512,
    [int]$MaxQueue = 8,
    [int]$KeepAliveSeconds = 60,
    [int]$OllamaPort = 11434,
    [string]$ApiTaskName = "ARGUS-API",
    [string]$WorkerTaskName = "ARGUS-Worker",
    [int]$ApiPort = 8787,
    [int]$WorkerProbePort = 8788,
    [switch]$RestartOllama,
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

function Set-EnvFileValues {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Values
    )

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

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $Uri -TimeoutSec 5
            return $response
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "Endpoint did not become ready within $TimeoutSeconds seconds: $Uri"
}

function Restart-OllamaServerSafely {
    param(
        [Parameter(Mandatory = $true)][string]$OllamaExe,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 1) {
        throw "ABORT: Ollama port $Port has multiple listener owners."
    }

    if ($listeners.Count -eq 1) {
        $ownerId = [int]$listeners[0].OwningProcess
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            throw "ABORT: Ollama listener PID $ownerId could not be inspected."
        }
        $executable = [string]$process.ExecutablePath
        $processName = [IO.Path]::GetFileNameWithoutExtension($executable)
        if ([string]::IsNullOrWhiteSpace($executable) -or $processName -notmatch '^ollama') {
            throw "ABORT: port $Port is not owned by an Ollama executable."
        }
        Stop-Process -Id $ownerId -Force
        $closeDeadline = (Get-Date).AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 500
            $remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        } while ($remaining.Count -gt 0 -and (Get-Date) -lt $closeDeadline)
        if ($remaining.Count -gt 0) {
            throw "Ollama listener did not stop cleanly on port $Port."
        }
    }

    Start-Process -FilePath $OllamaExe -ArgumentList @("serve") -WindowStyle Hidden
    Wait-HttpOk -Uri "http://127.0.0.1:$Port/api/tags" -TimeoutSeconds 60 | Out-Null
}

function Restart-ArgusTasks {
    foreach ($name in @($ApiTaskName, $WorkerTaskName)) {
        if ($null -eq (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
            throw "ARGUS scheduled task not found: $name"
        }
    }

    Stop-ScheduledTask -TaskName $ApiTaskName -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName $WorkerTaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $WorkerTaskName
    Start-Sleep -Seconds 1
    Start-ScheduledTask -TaskName $ApiTaskName

    $api = Wait-HttpOk -Uri "http://127.0.0.1:$ApiPort/v1/health" -TimeoutSeconds 120
    $worker = Wait-HttpOk -Uri "http://127.0.0.1:$WorkerProbePort/readyz" -TimeoutSeconds 120
    if ($api.status -ne "ok" -or $worker.status -ne "ok") {
        throw "ARGUS health verification failed after CPU tuning."
    }
}

if ($NumCtx -lt 2048 -or $NumCtx -gt 32768) { throw "NumCtx must be between 2048 and 32768." }
if ($NumPredict -lt 64 -or $NumPredict -gt 4096) { throw "NumPredict must be between 64 and 4096." }
if ($MaxQueue -lt 1 -or $MaxQueue -gt 128) { throw "MaxQueue must be between 1 and 128." }
if ($KeepAliveSeconds -lt 0 -or $KeepAliveSeconds -gt 3600) { throw "KeepAliveSeconds must be between 0 and 3600." }

$logicalProcessors = [Environment]::ProcessorCount
if ($logicalProcessors -lt 1) { $logicalProcessors = 1 }
$recommendedThreads = [Math]::Max(1, [Math]::Min(2, [int][Math]::Floor($logicalProcessors / 2.0)))
$resolvedThreads = if ($NumThread -gt 0) { $NumThread } else { $recommendedThreads }
if ($resolvedThreads -lt 1 -or $resolvedThreads -gt $logicalProcessors) {
    throw "NumThread must be between 1 and the detected logical processor count ($logicalProcessors)."
}

$ConfigFile = Join-Path $DataRoot "argus.env"
$BackupFile = Join-Path $DataRoot "argus.env.pre-ollama-cpu-tuning"
$StateFile = Join-Path $DataRoot "ollama-cpu-tuning.json"
$OllamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
if ($null -eq $OllamaCommand) {
    $OllamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
}
if ($null -eq $OllamaCommand) {
    throw "Ollama executable was not found in PATH."
}
$OllamaExe = $OllamaCommand.Source

$ollamaEnvironment = [ordered]@{
    OLLAMA_NUM_PARALLEL = "1"
    OLLAMA_MAX_LOADED_MODELS = "1"
    OLLAMA_MAX_QUEUE = [string]$MaxQueue
    OLLAMA_KEEP_ALIVE = "${KeepAliveSeconds}s"
    OLLAMA_CONTEXT_LENGTH = [string]$NumCtx
}
$argusEnvironment = [ordered]@{
    ARGUS_OLLAMA_MODEL = $TunedModel
    ARGUS_WORKER_CONCURRENCY = "1"
    ARGUS_MAX_CONCURRENCY = "2"
    ARGUS_BROWSER_MAX_CONCURRENCY = "1"
}

$plan = [ordered]@{
    Mode = if ($Apply) { "apply" } else { "plan-only" }
    LogicalProcessors = $logicalProcessors
    OllamaInferenceThreads = $resolvedThreads
    BaseModel = $BaseModel
    TunedModel = $TunedModel
    QwenThinking = "disabled_via_system_no_think"
    ContextTokens = $NumCtx
    MaxOutputTokens = $NumPredict
    OllamaParallelRequests = 1
    OllamaLoadedModels = 1
    OllamaQueueLimit = $MaxQueue
    OllamaKeepAliveSeconds = $KeepAliveSeconds
    ArgusWorkerConcurrency = 1
    ArgusFetchConcurrency = 2
    ArgusBrowserConcurrency = 1
    RestartOllama = [bool]$RestartOllama
    ConfigFile = $ConfigFile
}
$plan | ConvertTo-Json | Write-Host

if (-not $Apply) {
    Write-Host "Plan validation succeeded. No server resources were changed."
    exit 0
}

Assert-Administrator
if (-not (Test-Path -LiteralPath $ConfigFile -PathType Leaf)) {
    throw "ARGUS environment file not found: $ConfigFile"
}

if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
    Copy-Item -LiteralPath $ConfigFile -Destination $BackupFile
}

$previousMachineEnvironment = [ordered]@{}
foreach ($name in $ollamaEnvironment.Keys) {
    $previousMachineEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Machine")
    [Environment]::SetEnvironmentVariable($name, [string]$ollamaEnvironment[$name], "Machine")
    [Environment]::SetEnvironmentVariable($name, [string]$ollamaEnvironment[$name], "Process")
}

$tempModelFile = Join-Path ([IO.Path]::GetTempPath()) ("argus-ollama-" + [Guid]::NewGuid().ToString("N") + ".Modelfile")
try {
    $modelFileContent = @"
FROM $BaseModel
PARAMETER num_thread $resolvedThreads
PARAMETER num_ctx $NumCtx
PARAMETER num_predict $NumPredict
PARAMETER temperature 0
SYSTEM """
/no_think
You are the local ARGUS structured research component. Follow the caller prompt exactly and return only the requested concise structured output. Never add chain-of-thought or an explanation unless the caller explicitly requests it.
"""
"@
    [IO.File]::WriteAllText($tempModelFile, $modelFileContent, [Text.UTF8Encoding]::new($false))

    & $OllamaExe show $BaseModel *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Base Ollama model is not installed: $BaseModel"
    }

    & $OllamaExe create $TunedModel -f $tempModelFile
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create tuned Ollama model: $TunedModel"
    }
}
finally {
    Remove-Item -LiteralPath $tempModelFile -Force -ErrorAction SilentlyContinue
}

if ($RestartOllama) {
    Restart-OllamaServerSafely -OllamaExe $OllamaExe -Port $OllamaPort
}
else {
    Write-Warning "OLLAMA_* server limits are persisted but require an Ollama server restart to take effect. The tuned model thread/output limits apply when ARGUS loads the tuned model."
}

& $OllamaExe stop $BaseModel *> $null
& $OllamaExe stop $TunedModel *> $null

Set-EnvFileValues -Path $ConfigFile -Values $argusEnvironment
Restart-ArgusTasks

$state = [ordered]@{
    schema_version = 1
    applied_at_utc = [DateTime]::UtcNow.ToString("o")
    logical_processors = $logicalProcessors
    ollama_inference_threads = $resolvedThreads
    base_model = $BaseModel
    tuned_model = $TunedModel
    qwen_thinking = "disabled_via_system_no_think"
    num_ctx = $NumCtx
    num_predict = $NumPredict
    max_queue = $MaxQueue
    keep_alive_seconds = $KeepAliveSeconds
    restart_ollama_requested = [bool]$RestartOllama
    previous_machine_environment = $previousMachineEnvironment
    argus_env_backup = $BackupFile
}
[IO.File]::WriteAllText(
    $StateFile,
    (($state | ConvertTo-Json -Depth 5) + "`n"),
    [Text.UTF8Encoding]::new($false)
)

Write-Host "ARGUS Ollama CPU profile applied."
Write-Host "Tuned model: $TunedModel"
Write-Host "Inference threads: $resolvedThreads of $logicalProcessors logical processors"
Write-Host "Qwen thinking: disabled for the derived ARGUS model"
Write-Host "ARGUS worker concurrency: 1"
Write-Host "ARGUS source concurrency: 2"
Write-Host "ARGUS browser concurrency: 1"
Write-Host "Ollama max parallel requests: 1"
Write-Host "Ollama queue limit: $MaxQueue"
Write-Host "State: $StateFile"
Write-Host "Current loaded models:"
& $OllamaExe ps
