param(
    [int]$Port = 8765,
    [string]$Runtime = '',
    [switch]$NoBrowser,
    [string]$UvPath = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
try {
    $existing = Find-ExistingGridWorkbench -Port $Port -Runtime $Runtime
    if ($existing) {
        Open-ExistingGridWorkbench $existing -Runtime $Runtime -NoBrowser:$NoBrowser
        exit 0
    }
    $pythonWindowless = Initialize-GridEnvironment -Runtime $Runtime -NoUI:$NoBrowser -UvPath $UvPath
    $script:SetupStage = 'launch_windowless'
    $entryScript = Join-Path $PSScriptRoot 'launch.pyw'
    if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'Windowless Python missing' }
    $launchArguments = @('-X', 'utf8', $entryScript, '--port', [string]$Port)
    if ($Runtime) { $launchArguments += @('--runtime', $Runtime) }
    if ($NoBrowser) { $launchArguments += '--no-browser' }
    $quoted = ($launchArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    Start-Process -FilePath $pythonWindowless -ArgumentList $quoted -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
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
}
