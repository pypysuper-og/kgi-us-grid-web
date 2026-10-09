param(
    [int]$Port = 8765,
    [string]$Runtime = '',
    [switch]$NoBrowser,
    [string]$UvPath = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
$backend = $null
try {
    Initialize-SetupProgress -NoUI:$NoBrowser
    $logDirectory = Join-Path (Split-Path -Parent (Get-GridRuntime $Runtime)) 'logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $script:SetupLog = Join-Path $logDirectory 'bootstrap.jsonl'
    $script:SetupStage = 'probe_existing'
    Write-SetupEvent $script:SetupLog 'startup_started' @{ pid = $PID; port = $Port }
    Set-SetupProgress '正在啟動；先檢查是否已有工作台，再檢查必要環境。' -NoUI:$NoBrowser
    $existing = Find-ExistingGridWorkbench -Port $Port -Runtime $Runtime
    if ($script:SetupCancelRequested) {
        $script:SetupDetail = '已取消啟動；沒有啟動新的工作台。'
        throw 'Startup cancelled'
    }
    if ($existing) {
        Open-ExistingGridWorkbench $existing -Runtime $Runtime -NoBrowser:$NoBrowser
        exit 0
    }
    $pythonWindowless = Initialize-GridEnvironment -Runtime $Runtime -NoUI:$NoBrowser -UvPath $UvPath -KeepProgress
    $script:SetupStage = 'launch_windowless'
    $entryScript = Join-Path $PSScriptRoot 'launch.pyw'
    if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python missing' }
    # The visible launcher owns readiness, browser opening and startup errors.
    $launchArguments = @('-X', 'utf8', $entryScript, '--port', [string]$Port, '--no-browser')
    if ($Runtime) { $launchArguments += @('--runtime', $Runtime) }
    $quoted = ($launchArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $backend = Start-Process -FilePath $pythonWindowless -ArgumentList $quoted -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
    if (-not $NoBrowser) {
        $ready = Wait-GridWorkbench -Port $Port -Runtime $Runtime -Backend $backend
        $script:SetupStage = 'open_browser'
        Set-SetupProgress '工作台已就緒；正在開啟瀏覽器。'
        try { Start-Process -FilePath $ready.url }
        catch {
            $script:SetupDetail = "工作台已就緒，但瀏覽器未能自動開啟，請手動開啟 $($ready.url)"
            Write-SetupEvent $script:SetupLog 'workbench_browser_failed' @{ port = $Port; backend_pid = $ready.backend_pid }
            throw
        }
        Write-SetupEvent $script:SetupLog 'workbench_browser_opened' @{ port = $Port; backend_pid = $ready.backend_pid }
    }
} catch {
    if ($script:SetupStage -in @('environment_lock', 'runtime_lock')) {
        # Another launch may have become ready after the first read-only probe.
        $existing = Find-ExistingGridWorkbench -Port $Port -Runtime $Runtime
        if ($existing) {
            try {
                Open-ExistingGridWorkbench $existing -Runtime $Runtime -NoBrowser:$NoBrowser
                exit 0
            } catch { }
        } elseif ($script:SetupStage -eq 'environment_lock') {
            $script:SetupDetail = '環境正由其他工作台或安裝程序使用；目前未確認指定帳本與連接埠的介面已就緒。請回到既有頁面，或稍後再執行 start；不要刪除鎖檔或在使用中重裝。'
        }
    }
    Show-SetupFailure -NoUI:$NoBrowser
    exit 1
} finally {
    Close-SetupProgress
    if ($backend) { $backend.Dispose() }
}
