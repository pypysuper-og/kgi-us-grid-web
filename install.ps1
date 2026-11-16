param([string]$Runtime = '', [switch]$NoUI, [switch]$Repair, [string]$UvPath = '')
$ErrorActionPreference = 'Stop'
if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
    $message = 'PowerShell 正處於受限語言模式，無法安裝。請保留 .bootstrap/startup-failure.json 並核對 Windows 應用程式控制事件；不要關閉防護。'
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot '.bootstrap') -Force | Out-Null
    @{ event = 'startup_diagnostic'; stage = 'script_host'; language_mode = "$($ExecutionContext.SessionState.LanguageMode)"; sac_cause = 'NOT_ESTABLISHED' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot '.bootstrap/startup-failure.json') -Encoding UTF8
    Write-Host $message
    if (-not $NoUI) { try { (New-Object -ComObject WScript.Shell).Popup($message, 0, '美股網格工作台', 16) | Out-Null } catch { } }
    exit 1
}
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
try {
    $null = Initialize-GridEnvironment -Runtime $Runtime -NoUI:$NoUI -Repair:$Repair -UvPath $UvPath
    if (-not $NoUI) {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show('環境已準備完成。請雙擊 start.vbs 開啟工作台。', 'KGI 美股網格工作台') | Out-Null
    }
} catch { Show-SetupFailure -NoUI:$NoUI; exit 1 }
