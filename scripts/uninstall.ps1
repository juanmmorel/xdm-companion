$ErrorActionPreference = "Continue"
$companionDir = Join-Path $env:USERPROFILE ".xdman\companion"
$mainScript = Join-Path $companionDir "core\main.py"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

try {
  Remove-ItemProperty -LiteralPath $runKey -Name "XDMCompanion" -ErrorAction SilentlyContinue
} catch { }

if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
  $target = [IO.Path]::GetFullPath($mainScript)
  $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine.IndexOf(
        $target,
        [StringComparison]::OrdinalIgnoreCase
      ) -ge 0
    }
  foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

Start-Sleep -Milliseconds 500
if (Test-Path -LiteralPath $companionDir) {
  Remove-Item -LiteralPath $companionDir -Recurse -Force -ErrorAction Stop
}
Write-Host "[*] XDM Companion se desinstaló. XDM y la extensión del navegador permanecen instalados."
