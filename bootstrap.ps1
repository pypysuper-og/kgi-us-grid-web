# Shared setup for both install and start. Windows PowerShell 5.1 or later.
$ErrorActionPreference = 'Stop'

function Get-GridRuntime([string]$Runtime) {
    if ($Runtime) { return [IO.Path]::GetFullPath($Runtime) }
    if ($env:KGI_GRID_RUNTIME) { return [IO.Path]::GetFullPath($env:KGI_GRID_RUNTIME) }
    return Join-Path $env:LOCALAPPDATA 'KGI_US_Grid_Trading_Web\runtime'
}

function Write-SetupEvent([string]$Log, [string]$Event, [hashtable]$Fields = @{}) {
    $record = @{ at = (Get-Date).ToUniversalTime().ToString('o'); event = $Event }
    foreach ($key in $Fields.Keys) { $record[$key] = $Fields[$key] }
    $record | ConvertTo-Json -Compress | Add-Content -LiteralPath $Log -Encoding UTF8
}

function ConvertTo-NativeArgument([string]$Value) {
    # Windows CommandLineToArgvW escaping; no cmd.exe or string evaluation.
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Invoke-SetupProcess([string]$File, [string[]]$Arguments, [string]$Log,
                             [string]$Stage, [switch]$AllowFailure, [int]$Timeout = 900) {
    Write-SetupEvent $Log 'setup_call' @{ stage = $Stage }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $info.WorkingDirectory = $PSScriptRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Unable to launch setup process' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
        while (-not $process.WaitForExit(200)) {
            if ($script:SetupForm) { [Windows.Forms.Application]::DoEvents() }
            if ([DateTime]::UtcNow -gt $deadline) {
                $process.Kill()
                throw "Setup timeout at $Stage"
            }
        }
        # Never persist arbitrary subprocess output; download/import logs may contain private paths.
        $null = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        $code = $process.ExitCode
        Write-SetupEvent $Log 'setup_result' @{ stage = $Stage; exit_code = $code }
        if ($code -ne 0 -and -not $AllowFailure) { throw "Setup failed at $Stage (exit $code)" }
        return $code
    } finally { $process.Dispose() }
}

function Set-SetupProgress([string]$Text, [switch]$NoUI) {
    if ($NoUI) { Write-Host $Text; return }
    if (-not $script:SetupForm) {
        Add-Type -AssemblyName System.Windows.Forms
        $script:SetupForm = New-Object Windows.Forms.Form
        $script:SetupForm.Text = 'KGI 美股網格工作台：準備環境'
        $script:SetupForm.ClientSize = New-Object Drawing.Size(520, 145)
        $script:SetupForm.StartPosition = 'CenterScreen'
        $script:SetupForm.FormBorderStyle = 'FixedDialog'
        $script:SetupForm.ControlBox = $false
        $script:SetupLabel = New-Object Windows.Forms.Label
        $script:SetupLabel.Location = New-Object Drawing.Point(20, 20)
        $script:SetupLabel.Size = New-Object Drawing.Size(480, 55)
        $bar = New-Object Windows.Forms.ProgressBar
        $bar.Location = New-Object Drawing.Point(20, 90)
        $bar.Size = New-Object Drawing.Size(480, 22)
        $bar.Style = 'Marquee'
        $script:SetupForm.Controls.AddRange(@($script:SetupLabel, $bar))
        $script:SetupForm.Show()
    }
    $script:SetupLabel.Text = $Text
    [Windows.Forms.Application]::DoEvents()
}

function Initialize-GridEnvironment([string]$Runtime = '', [switch]$NoUI, [switch]$Repair,
                                     [string]$UvPath = '') {
    if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'Windows x64 is required'
    }
    $directory = Join-Path $PSScriptRoot '.bootstrap'
    $logDirectory = Join-Path (Split-Path -Parent (Get-GridRuntime $Runtime)) 'logs'
    New-Item -ItemType Directory -Path $directory, $logDirectory -Force | Out-Null
    $log = Join-Path $logDirectory 'bootstrap.jsonl'
    $script:SetupLog = $log
    $script:SetupForm = $null
    $script:SetupStage = 'environment_lock'
    $environmentLock = $null
    $runtimeLock = $null
    try {
        # Serializes environment mutation and check-to-launch handoff in this source copy.
        $environmentLock = [IO.File]::Open((Join-Path $directory 'install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        $checker = Join-Path $PSScriptRoot 'tools\check_environment.py'
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONNOUSERSITE = '1'
        if (-not $Repair -and (Test-Path -LiteralPath $python)) {
            $script:SetupStage = 'environment_check'
            $check = Invoke-SetupProcess $python @('-X', 'utf8', $checker) $log $script:SetupStage -AllowFailure -Timeout 45
            if ($check -eq 0) {
                Write-SetupEvent $log 'environment_ready' @{ installed = $false }
                return Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
            }
        }
        # Refuse to mutate this environment while its selected runtime is active.
        $script:SetupStage = 'runtime_lock'
        $runtimeDirectory = Get-GridRuntime $Runtime
        New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
        $runtimeLock = [IO.File]::Open((Join-Path $runtimeDirectory 'instance.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        Set-SetupProgress '正在檢查 uv；首次準備需要網路，通常需數分鐘。' -NoUI:$NoUI
        $script:SetupStage = 'uv_prepare'
        if (-not $UvPath) {
            $localUv = Join-Path $directory 'uv-0.9.25\uv.exe'
            $existing = Get-Command uv.exe -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath $localUv) { $UvPath = $localUv }
            elseif ($existing) { $UvPath = $existing.Source }
            else {
                Set-SetupProgress '正在從 Astral 官方發佈下載 uv，並核對 SHA-256。' -NoUI:$NoUI
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                $archive = Join-Path $directory 'uv-0.9.25.zip'
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri 'https://github.com/astral-sh/uv/releases/download/0.9.25/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
                $expected = 'd63f8e59cf76bcce9cb8a3eac6c1a89adce0f89a29bacca978c9bf842f419277'
                if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
                    throw 'uv archive checksum mismatch'
                }
                Expand-Archive -LiteralPath $archive -DestinationPath (Split-Path -Parent $localUv) -Force
                $UvPath = $localUv
            }
        }
        $script:SetupStage = 'environment_sync'
        Set-SetupProgress '正在建立 Python 3.12 環境並安裝鎖定套件（含 SuperPy）。' -NoUI:$NoUI
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $directory 'python'
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot '.venv'
        $env:UV_PYTHON_DOWNLOADS = 'automatic'
        $arguments = @('sync', '--locked', '--extra', 'broker', '--no-dev', '--managed-python', '--python', '3.12', '--no-progress')
        if ($Repair) { $arguments += '--reinstall' }
        $null = Invoke-SetupProcess $UvPath $arguments $log $script:SetupStage
        $script:SetupStage = 'environment_verify'
        Set-SetupProgress '正在驗證 Python、套件與隱藏啟動元件；不會登入券商。' -NoUI:$NoUI
        $null = Invoke-SetupProcess $python @('-X', 'utf8', $checker, '--verify', '--write-marker') $log $script:SetupStage -Timeout 120
        Write-SetupEvent $log 'environment_ready' @{ installed = $true }
        return Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    } catch {
        Write-SetupEvent $log 'bootstrap_failed' @{ stage = $script:SetupStage; error_type = $_.Exception.GetType().Name }
        throw
    } finally {
        if ($runtimeLock) { $runtimeLock.Dispose() }
        if ($environmentLock) { $environmentLock.Dispose() }
        if ($script:SetupForm) { $script:SetupForm.Close(); $script:SetupForm.Dispose(); $script:SetupForm = $null }
    }
}

function Show-SetupFailure([switch]$NoUI) {
    if ($NoUI) { return }
    Add-Type -AssemblyName System.Windows.Forms
    $message = "環境準備或啟動未完成（階段：$script:SetupStage）。`n請確認網路及資料夾可寫入，並先結束使用中的工作台。`n可執行 install.vbs 重新檢查；需要重裝套件時用 install.vbs -Repair。`n紀錄：$script:SetupLog"
    [Windows.Forms.MessageBox]::Show($message, 'KGI 美股網格工作台', 'OK', 'Error') | Out-Null
}
