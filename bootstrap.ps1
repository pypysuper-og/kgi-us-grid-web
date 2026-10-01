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

function Protect-SetupOutput([string]$Text) {
    $Text = $Text -replace '\x1b\[[0-?]*[ -/]*[@-~]', ''
    if ($Text -match '(?i)password|secret|token|api[_-]?key|credential|authorization') {
        return '[已遮蔽含敏感欄位的安裝訊息]'
    }
    # Only uv setup output is displayed. SDK import output remains suppressed.
    $Text = [regex]::Replace($Text, 'https?://[^\s<>"'']+', {
        param($match)
        try { return ([uri]$match.Value).GetLeftPart([UriPartial]::Authority) -replace '//[^/@]+@', '//' }
        catch { return '[下載網址]' }
    })
    foreach ($path in @($PSScriptRoot, $env:USERPROFILE)) {
        if ($path) { $Text = [regex]::Replace($Text, [regex]::Escape($path), '[本機路徑]', 'IgnoreCase') }
    }
    $Text = $Text -replace '(?i)([''"])(?:[a-z]:[\\/]|\\\\).*?\1', '[本機路徑]'
    $Text = $Text -replace '(?i)(?<![a-z0-9])(?:[a-z]:[\\/]|\\\\)[^\s''"]+', '[本機路徑]'
    return $Text.Substring(0, [Math]::Min($Text.Length, 1200))
}

function Add-SetupMessage([string]$Text) {
    $script:SetupLastActivity = [DateTime]::UtcNow
    $script:SetupMessages = @($script:SetupMessages + $Text | Select-Object -Last 120)
    if ($script:SetupNoUI) { Write-Host $Text }
    elseif ($script:SetupOutput) {
        $script:SetupOutput.Lines = [string[]]$script:SetupMessages
        $script:SetupOutput.SelectionStart = $script:SetupOutput.TextLength
        $script:SetupOutput.ScrollToCaret()
    }
}

function Update-SetupProgress {
    if ($script:SetupStatus) {
        $elapsed = [DateTime]::UtcNow - $script:SetupStarted
        $idle = [int]([DateTime]::UtcNow - $script:SetupLastActivity).TotalSeconds
        $script:SetupStatus.Text = ('已經過 {0} 分 {1} 秒；最近訊息：{2} 秒前。' -f $elapsed.Minutes, $elapsed.Seconds, $idle)
        if ($idle -ge 30) { $script:SetupStatus.Text += '  正在等待下載或檢查；可取消後重試。' }
    }
    if ($script:SetupForm) { [Windows.Forms.Application]::DoEvents() }
}

function Receive-SetupLine([string]$Text, [string]$Log, [string]$Stage, [switch]$ShowOutput) {
    if ($ShowOutput) {
        $safe = Protect-SetupOutput $Text
        if (-not $safe.Trim()) { return }
        Add-SetupMessage $safe
        $script:SetupDetail = $safe
        Write-SetupEvent $Log 'setup_output' @{ stage = $Stage; message = $safe }
    } else {
        # The checker emits an allowlisted reason, never its native SDK exception.
        try { $probe = $Text | ConvertFrom-Json } catch { return }
        $reasons = @{
            receipt_missing = '既有環境缺少安裝收據；將核對套件，保留相容的 Python。'
            receipt_invalid = '安裝收據不完整；將重新核對環境。'
            environment_changed = '環境或套件需求已變更；將核對鎖定版本。'
            python_version_or_architecture = '現有 Python 不符合 3.12 x64；將準備相容版本。'
            wrong_virtual_environment = 'Python 不屬於本專案環境。'
            windowless_python_missing = '缺少隱藏啟動用的 pythonw.exe。'
            broker_version = 'SuperPy 版本不符合鎖定需求。'
            dependency_check_failed = '必要套件檢查失敗；請使用修復安裝，或查看安裝錯誤。'
        }
        if ($probe.reason -and $reasons.ContainsKey([string]$probe.reason)) {
            $script:SetupDetail = $reasons[[string]$probe.reason]
            Add-SetupMessage $script:SetupDetail
            Write-SetupEvent $Log 'environment_check_reason' @{ stage = $Stage; reason = [string]$probe.reason }
        }
    }
}

function Invoke-SetupProcess([string]$File, [string[]]$Arguments, [string]$Log,
                             [string]$Stage, [switch]$AllowFailure, [int]$Timeout = 900,
                             [switch]$ShowOutput) {
    Write-SetupEvent $Log 'setup_call' @{ stage = $Stage }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $info.WorkingDirectory = $PSScriptRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'Unable to launch setup process' }
        $readers = @($process.StandardOutput, $process.StandardError)
        $pending = @($readers[0].ReadLineAsync(), $readers[1].ReadLineAsync())
        $closed = @($false, $false)
        $deadline = [DateTime]::UtcNow.AddSeconds($Timeout)
        while ($true) {
            for ($i = 0; $i -lt 2; $i++) {
                while (-not $closed[$i] -and $pending[$i].IsCompleted) {
                    $line = $pending[$i].GetAwaiter().GetResult()
                    if ($null -eq $line) { $closed[$i] = $true; break }
                    Receive-SetupLine $line $Log $Stage -ShowOutput:$ShowOutput
                    $pending[$i] = $readers[$i].ReadLineAsync()
                }
            }
            Update-SetupProgress
            if ($script:SetupCancelRequested) {
                if (-not $process.HasExited) { $process.Kill(); $null = $process.WaitForExit(5000) }
                $script:SetupDetail = '已取消環境準備；沒有啟動工作台。重新執行 start 可接續檢查。'
                Write-SetupEvent $Log 'setup_cancelled' @{ stage = $Stage }
                throw 'Setup cancelled'
            }
            if ([DateTime]::UtcNow -gt $deadline) {
                if (-not $process.HasExited) { $process.Kill(); $null = $process.WaitForExit(5000) }
                $script:SetupDetail = "此階段超過 $Timeout 秒；請確認下載網路後重試。"
                Write-SetupEvent $Log 'setup_timeout' @{ stage = $Stage; timeout_seconds = $Timeout }
                throw "Setup timeout at $Stage"
            }
            if ($process.HasExited -and $closed[0] -and $closed[1]) { break }
            [Threading.Thread]::Sleep(100)
        }
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
        $script:SetupForm.ClientSize = New-Object Drawing.Size(720, 390)
        $script:SetupForm.StartPosition = 'CenterScreen'
        $script:SetupForm.FormBorderStyle = 'FixedDialog'
        $script:SetupForm.ControlBox = $false
        $script:SetupLabel = New-Object Windows.Forms.Label
        $script:SetupLabel.Location = New-Object Drawing.Point(20, 20)
        $script:SetupLabel.Size = New-Object Drawing.Size(680, 45)
        $script:SetupStatus = New-Object Windows.Forms.Label
        $script:SetupStatus.Location = New-Object Drawing.Point(20, 65)
        $script:SetupStatus.Size = New-Object Drawing.Size(680, 32)
        $script:SetupOutput = New-Object Windows.Forms.TextBox
        $script:SetupOutput.Location = New-Object Drawing.Point(20, 105)
        $script:SetupOutput.Size = New-Object Drawing.Size(680, 200)
        $script:SetupOutput.Multiline = $true
        $script:SetupOutput.ReadOnly = $true
        $script:SetupOutput.ScrollBars = 'Vertical'
        $bar = New-Object Windows.Forms.ProgressBar
        $bar.Location = New-Object Drawing.Point(20, 315)
        $bar.Size = New-Object Drawing.Size(680, 18)
        $bar.Style = 'Marquee'
        $cancel = New-Object Windows.Forms.Button
        $cancel.Text = '取消準備'
        $cancel.Location = New-Object Drawing.Point(595, 345)
        $cancel.Size = New-Object Drawing.Size(105, 28)
        $cancel.Add_Click({ $script:SetupCancelRequested = $true })
        $note = New-Object Windows.Forms.Label
        $note.Text = '首次下載可能需數分鐘；套件安裝最多等待 15 分鐘。不會登入券商。'
        $note.Location = New-Object Drawing.Point(20, 350)
        $note.Size = New-Object Drawing.Size(570, 30)
        $script:SetupForm.Controls.AddRange(@($script:SetupLabel, $script:SetupStatus, $script:SetupOutput, $bar, $cancel, $note))
        $script:SetupForm.Show()
    }
    $script:SetupLabel.Text = $Text
    Add-SetupMessage $Text
    Update-SetupProgress
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
    $script:SetupOutput = $null
    $script:SetupStatus = $null
    $script:SetupNoUI = [bool]$NoUI
    $script:SetupStarted = [DateTime]::UtcNow
    $script:SetupLastActivity = $script:SetupStarted
    $script:SetupMessages = @()
    $script:SetupCancelRequested = $false
    $script:SetupDetail = ''
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
        Set-SetupProgress '正在檢查既有環境；已準備完整就直接啟動。' -NoUI:$NoUI
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
                $client = New-Object Net.WebClient
                try {
                    $download = $client.DownloadFileTaskAsync([uri]'https://github.com/astral-sh/uv/releases/download/0.9.25/uv-x86_64-pc-windows-msvc.zip', $archive)
                    $deadline = [DateTime]::UtcNow.AddSeconds(120)
                    while (-not $download.IsCompleted) {
                        Update-SetupProgress
                        if ($script:SetupCancelRequested -or [DateTime]::UtcNow -gt $deadline) {
                            $client.CancelAsync()
                            throw 'uv download cancelled or timed out'
                        }
                        [Threading.Thread]::Sleep(200)
                    }
                    $null = $download.GetAwaiter().GetResult()
                } finally { $client.Dispose() }
                $expected = 'd63f8e59cf76bcce9cb8a3eac6c1a89adce0f89a29bacca978c9bf842f419277'
                if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
                    throw 'uv archive checksum mismatch'
                }
                Expand-Archive -LiteralPath $archive -DestinationPath (Split-Path -Parent $localUv) -Force
                $UvPath = $localUv
            }
        }
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $directory 'python'
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot '.venv'
        $env:UV_PYTHON_DOWNLOADS = 'automatic'
        $pythonRequest = '3.12'
        $reusedPython = $false
        if (Test-Path -LiteralPath $python) {
            $script:SetupStage = 'python_check'
            $probe = Invoke-SetupProcess $python @('-X', 'utf8', $checker, '--python-only') $log $script:SetupStage -AllowFailure -Timeout 30
            if ($probe -eq 0) { $pythonRequest = $python; $reusedPython = $true }
        }
        $arguments = @('sync', '--locked', '--extra', 'broker', '--no-dev', '--python', $pythonRequest, '--no-progress')
        $needsSync = $true
        if ($reusedPython -and -not $Repair) {
            $script:SetupStage = 'environment_lock_check'
            Set-SetupProgress '已找到相容的 Python 3.12；正在核對鎖定套件，無須重新下載 Python。' -NoUI:$NoUI
            $needsSync = (Invoke-SetupProcess $UvPath ($arguments + '--check') $log $script:SetupStage -AllowFailure -ShowOutput -Timeout 90) -ne 0
        }
        if ($Repair) { $arguments += '--reinstall' }
        if ($needsSync) {
            $script:SetupStage = 'environment_sync'
            Set-SetupProgress '正在準備 Python 3.12 與鎖定套件（含 SuperPy）；以下顯示即時安裝訊息。' -NoUI:$NoUI
            $script:SetupDetail = ''
            $null = Invoke-SetupProcess $UvPath $arguments $log $script:SetupStage -ShowOutput
        }
        $script:SetupStage = 'environment_verify'
        Set-SetupProgress '正在驗證 Python、套件與隱藏啟動元件；不會登入券商。' -NoUI:$NoUI
        $null = Invoke-SetupProcess $python @('-X', 'utf8', $checker, '--verify', '--write-marker') $log $script:SetupStage -Timeout 120
        Write-SetupEvent $log 'environment_ready' @{ installed = $needsSync; reused_python = $reusedPython }
        return Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    } catch {
        if (-not $script:SetupDetail) { $script:SetupDetail = Protect-SetupOutput $_.Exception.Message }
        Write-SetupEvent $log 'bootstrap_failed' @{ stage = $script:SetupStage; error_type = $_.Exception.GetType().Name; message = $script:SetupDetail }
        throw
    } finally {
        if ($runtimeLock) { $runtimeLock.Dispose() }
        if ($environmentLock) { $environmentLock.Dispose() }
        if ($script:SetupForm) { $script:SetupForm.Close(); $script:SetupForm.Dispose(); $script:SetupForm = $null }
    }
}

function Show-SetupFailure([switch]$NoUI) {
    $message = "環境準備或啟動未完成（階段：$script:SetupStage）。`n$script:SetupDetail`n可執行 start 重新檢查；需要重裝套件時用 install.vbs -Repair。`n紀錄：$script:SetupLog"
    if ($NoUI) { Write-Host $message; return }
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show($message, 'KGI 美股網格工作台', 'OK', 'Error') | Out-Null
}
