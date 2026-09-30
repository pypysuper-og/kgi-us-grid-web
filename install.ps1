param([string]$Runtime = '', [switch]$NoUI, [switch]$Repair, [string]$UvPath = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
try {
    $null = Initialize-GridEnvironment -Runtime $Runtime -NoUI:$NoUI -Repair:$Repair -UvPath $UvPath
    if (-not $NoUI) {
        Add-Type -AssemblyName System.Windows.Forms
        [Windows.Forms.MessageBox]::Show('環境已準備完成。請雙擊 start.vbs 開啟工作台。', 'KGI 美股網格工作台') | Out-Null
    }
} catch { Show-SetupFailure -NoUI:$NoUI; exit 1 }
