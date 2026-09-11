[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$OllamaExecutable = "ollama.exe",
    [ValidateRange(1, 62)]
    [int]$CpuCount = 2,
    [string]$BaseModel = "qwen3:8b",
    [string]$ArgusModel = "argus-qwen3:8b-cpu",
    [switch]$SkipModelCreate,
    [switch]$DoNotStartServer
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Запустите PowerShell от имени администратора."
    }
}

function Invoke-Ollama {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & $OllamaExecutable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Команда Ollama завершилась с кодом $LASTEXITCODE."
    }
}

function Set-OllamaProcessLimits {
    param([Parameter(Mandatory = $true)]$Process)

    $logicalCpuCount = [Environment]::ProcessorCount
    $selectedCpuCount = [Math]::Min($CpuCount, [Math]::Min($logicalCpuCount, 62))
    $affinityMask = ([int64]1 -shl $selectedCpuCount) - 1
    $Process.PriorityClass = [Diagnostics.ProcessPriorityClass]::BelowNormal
    $Process.ProcessorAffinity = [IntPtr]$affinityMask
    Write-Host "Ollama PID=$($Process.Id): priority=BelowNormal, CPU mask=0x$($affinityMask.ToString('X'))."
}

Assert-Administrator

$ollamaCommand = Get-Command $OllamaExecutable -ErrorAction SilentlyContinue
if ($null -eq $ollamaCommand) {
    throw "Ollama не найдена: $OllamaExecutable"
}
$OllamaExecutable = $ollamaCommand.Source

$ollamaEnvironment = [ordered]@{
    OLLAMA_NUM_PARALLEL = "1"
    OLLAMA_MAX_LOADED_MODELS = "1"
    OLLAMA_MAX_QUEUE = "4"
    OLLAMA_KEEP_ALIVE = "60s"
}

foreach ($entry in $ollamaEnvironment.GetEnumerator()) {
    if ($PSCmdlet.ShouldProcess("Machine/Process environment", "Set $($entry.Key)=$($entry.Value)")) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Machine")
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        Write-Host "$($entry.Key)=$($entry.Value)"
    }
}

if (-not $SkipModelCreate) {
    $modelFile = Join-Path ([IO.Path]::GetTempPath()) "argus-qwen3-cpu.Modelfile"
    try {
        [IO.File]::WriteAllLines(
            $modelFile,
            @(
                "FROM $BaseModel",
                "PARAMETER num_ctx 4096",
                "PARAMETER num_predict 512",
                "PARAMETER temperature 0"
            ),
            [Text.UTF8Encoding]::new($false)
        )
        if ($PSCmdlet.ShouldProcess($BaseModel, "Загрузить базовую модель Ollama")) {
            Invoke-Ollama -Arguments @("pull", $BaseModel)
        }
        if ($PSCmdlet.ShouldProcess($ArgusModel, "Создать ограниченную модель ARGUS")) {
            Invoke-Ollama -Arguments @("create", $ArgusModel, "-f", $modelFile)
        }
    }
    finally {
        Remove-Item -LiteralPath $modelFile -Force -ErrorAction SilentlyContinue
    }
}

if ($DoNotStartServer) {
    Write-Host "Параметры сохранены. Перезапустите Ollama и повторно запустите скрипт с -SkipModelCreate для применения priority/affinity."
    exit 0
}

if ($PSCmdlet.ShouldProcess("Ollama", "Перезапустить сервер с ограничениями ARGUS")) {
    Get-Process -Name "ollama" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction Stop
    $server = Start-Process -FilePath $OllamaExecutable -ArgumentList @("serve") -PassThru -WindowStyle Hidden
    Start-Sleep -Milliseconds 750
    $server.Refresh()
    if ($server.HasExited) {
        throw "Ollama завершилась до применения ограничений процесса."
    }
    Set-OllamaProcessLimits -Process $server
}

Write-Host "Профиль Ollama для ARGUS настроен."
