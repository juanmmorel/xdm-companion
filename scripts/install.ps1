[CmdletBinding()]
param(
  [string]$Repository = "juanmmorel/xdm-companion",
  [string]$Version = "latest",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$companionDir = Join-Path $env:USERPROFILE ".xdman\companion"
$appDataDir = Join-Path $env:LOCALAPPDATA "XDM Companion"
$logDir = Join-Path $appDataDir "logs"
$logFile = Join-Path $appDataDir "install.log"
$transcriptStarted = $false

function Write-Info([string]$Message) {
  Write-Host "[*] $Message"
}

function Stop-RunningCompanion([string]$MainScript) {
  $stopped = $false
  if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) {
    return $stopped
  }
  $target = [IO.Path]::GetFullPath($MainScript)
  $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine.IndexOf(
        $target,
        [StringComparison]::OrdinalIgnoreCase
      ) -ge 0
    }
  foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    $stopped = $true
  }
  if ($stopped) {
    Start-Sleep -Milliseconds 700
  }
  return $stopped
}

function Find-Python {
  $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
  if ($launcher) {
    $candidate = & $launcher.Source -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath $candidate.Trim())) {
      return $candidate.Trim()
    }
  }

  foreach ($commandName in @("python.exe", "python3.exe")) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue |
      Where-Object { $_.Source -notlike "*\WindowsApps\*" } |
      Select-Object -First 1
    if ($command) {
      $candidate = & $command.Source -c "import sys; print(sys.executable)" 2>$null
      if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath $candidate.Trim())) {
        return $candidate.Trim()
      }
    }
  }

  $pythonRoots = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python"),
    $env:ProgramFiles
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
  foreach ($root in $pythonRoots) {
    $pythonDirectories = Get-ChildItem -LiteralPath $root -Directory -Filter "Python*" -ErrorAction SilentlyContinue |
      Sort-Object Name -Descending
    foreach ($directory in $pythonDirectories) {
      $candidate = Join-Path $directory.FullName "python.exe"
      if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
  }
  return $null
}

function Ensure-Python {
  $python = Find-Python
  if (-not $python) {
    $winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
    if (-not $winget) {
      throw "No se encontró Python 3 ni winget. Instala Python 3.10 o superior y vuelve a ejecutar este script."
    }
    Write-Info "No se encontró Python; instalando Python 3.14 para este usuario con winget."
    & $winget.Source install --id Python.Python.3.14 --exact --source winget --silent `
      --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
      throw "winget no pudo instalar Python. Instala Python 3.10 o superior y vuelve a ejecutar el instalador."
    }
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
    $python = Find-Python
  }
  if (-not $python) {
    throw "Python se instaló, pero no se pudo localizar python.exe. Cierra y vuelve a abrir PowerShell, y ejecuta de nuevo el instalador."
  }

  $versionText = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "No se pudo iniciar Python: $python"
  }
  $versionParts = $versionText.Trim().Split(".")
  if ($versionParts.Count -lt 2 -or [int]$versionParts[0] -lt 3 -or
      ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 10)) {
    throw "Se requiere Python 3.10 o superior; se encontró $versionText."
  }
  return $python
}

function Get-ValidatedRelease([string]$Repo, [string]$RequestedVersion) {
  if ($Repo -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "Repositorio no válido: $Repo"
  }
  if ($RequestedVersion -eq "latest") {
    $apiUrl = "https://api.github.com/repos/$Repo/releases/latest"
  } else {
    if ($RequestedVersion -notmatch "^v?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$") {
      throw "Versión no válida: $RequestedVersion"
    }
    $tag = [Uri]::EscapeDataString($RequestedVersion)
    $apiUrl = "https://api.github.com/repos/$Repo/releases/tags/$tag"
  }

  $release = Invoke-RestMethod -Uri $apiUrl -Headers @{
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "XDM-Companion-Installer"
  }
  if ($release.draft -or $release.prerelease) {
    throw "GitHub devolvió una versión que no es una release estable publicada."
  }
  if ($RequestedVersion -ne "latest" -and $release.tag_name -ne $RequestedVersion) {
    throw "GitHub devolvió $($release.tag_name), no $RequestedVersion."
  }
  if ($release.tag_name -notmatch "^v?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$") {
    throw "GitHub devolvió una etiqueta de versión no válida."
  }

  $assetName = "xdm-companion-windows.zip"
  $baseUrl = "https://github.com/$Repo/releases/download/$($release.tag_name)/"
  $urls = @()
  foreach ($name in @($assetName, "$assetName.sha256")) {
    $asset = $release.assets | Where-Object { $_.name -eq $name } | Select-Object -First 1
    if (-not $asset -or $asset.browser_download_url -ne "$baseUrl$name") {
      throw "La release $($release.tag_name) no contiene el archivo esperado $name."
    }
    $urls += $asset.browser_download_url
  }
  return @{ Tag = $release.tag_name; Archive = $urls[0]; Checksum = $urls[1] }
}

function Test-SafeZip([string]$ArchivePath, [string]$Destination) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $root = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
  $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
  try {
    foreach ($entry in $archive.Entries) {
      $relative = $entry.FullName.Replace("/", [IO.Path]::DirectorySeparatorChar)
      if ([IO.Path]::IsPathRooted($relative) -or $relative.Split([IO.Path]::DirectorySeparatorChar) -contains "..") {
        throw "El paquete contiene una ruta insegura: $($entry.FullName)"
      }
      $target = [IO.Path]::GetFullPath((Join-Path $Destination $relative))
      if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El paquete contiene una ruta fuera de la carpeta destino: $($entry.FullName)"
      }
      $unixMode = ($entry.ExternalAttributes -shr 16) -band 0xF000
      if ($unixMode -eq 0xA000) {
        throw "El paquete contiene un enlace no permitido: $($entry.FullName)"
      }
    }
  } finally {
    $archive.Dispose()
  }
}

function Get-SupportedRuntime {
  foreach ($name in @("deno.exe", "node.exe")) {
    $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { continue }
    $output = & $command.Source --version 2>$null
    if ($LASTEXITCODE -ne 0) { continue }
    $match = [regex]::Match(($output -join " "), "(\d+)\.(\d+)")
    if (-not $match.Success) { continue }
    $version = [int]$match.Groups[1].Value * 1000 + [int]$match.Groups[2].Value
    $minimum = if ($name -eq "deno.exe") { 2003 } else { 22000 }
    if ($version -ge $minimum) { return $true }
  }
  return $false
}

function Install-LocalDeno([string]$WorkDir) {
  $architecture = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
  } else {
    $env:PROCESSOR_ARCHITECTURE
  }
  $target = switch ($architecture) {
    "AMD64" { "x86_64-pc-windows-msvc"; break }
    "ARM64" { "aarch64-pc-windows-msvc"; break }
    default { throw "Arquitectura de Windows no compatible: $architecture" }
  }
  $apiUrl = "https://api.github.com/repos/denoland/deno/releases/latest"
  $release = Invoke-RestMethod -Uri $apiUrl -Headers @{
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "XDM-Companion-Installer"
  }
  $assetName = "deno-$target.zip"
  $baseUrl = "https://github.com/denoland/deno/releases/download/$($release.tag_name)/"
  $zipAsset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
  $sumAsset = $release.assets | Where-Object { $_.name -eq "$assetName.sha256sum" } | Select-Object -First 1
  if (-not $release.tag_name -or -not $zipAsset -or -not $sumAsset -or
      $release.draft -or $release.prerelease -or
      $release.tag_name -notmatch "^v\d+\.\d+\.\d+$" -or
      $zipAsset.browser_download_url -ne "$baseUrl$assetName" -or
      $sumAsset.browser_download_url -ne "$baseUrl$assetName.sha256sum") {
    throw "No se pudo validar el paquete oficial de Deno para Windows."
  }

  $zipPath = Join-Path $WorkDir $assetName
  $sumPath = Join-Path $WorkDir "$assetName.sha256sum"
  Invoke-WebRequest -Uri $zipAsset.browser_download_url -OutFile $zipPath
  Invoke-WebRequest -Uri $sumAsset.browser_download_url -OutFile $sumPath
  $expected = [regex]::Match((Get-Content -LiteralPath $sumPath -Raw), "[0-9a-fA-F]{64}").Value
  $actual = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
  if (-not $expected -or $actual -ne $expected) {
    throw "La verificación SHA-256 de Deno falló."
  }

  $denoExtract = Join-Path $WorkDir "deno"
  New-Item -ItemType Directory -Path $denoExtract -Force | Out-Null
  Test-SafeZip $zipPath $denoExtract
  Expand-Archive -LiteralPath $zipPath -DestinationPath $denoExtract -Force
  $denoExe = Join-Path $denoExtract "deno.exe"
  $files = @(Get-ChildItem -LiteralPath $denoExtract -File -Recurse)
  if (-not (Test-Path -LiteralPath $denoExe) -or $files.Count -ne 1) {
    throw "El archivo de Deno tiene un contenido distinto del esperado."
  }

  $runtimeDir = Join-Path $companionDir "runtime"
  New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
  Copy-Item -LiteralPath $denoExe -Destination (Join-Path $runtimeDir "deno.exe") -Force
  $env:Path = "$runtimeDir;$env:Path"
  Write-Info "Deno $($release.tag_name) se preparó para XDM Companion."
}

function Start-Companion([string]$Python, [string]$MainScript, [string]$CoreDir) {
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
  $stdout = Join-Path $logDir "service-output.log"
  $stderr = Join-Path $logDir "service-error.log"
  Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
  $argument = '"' + $MainScript + '"'
  Start-Process -FilePath $Python -ArgumentList $argument -WorkingDirectory $CoreDir `
    -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:21121/health" -TimeoutSec 2
      if ($health.status -eq "ok") { return }
    } catch { }
    Start-Sleep -Seconds 1
  }
  $details = if (Test-Path -LiteralPath $stderr) {
    (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
  } else { "No se encontró el registro de errores del servicio." }
  throw "El servicio no respondió en 20 segundos. Revisa $stderr. $details"
}

if ($Uninstall) {
  $uninstaller = Join-Path $appDataDir "uninstall.ps1"
  if (-not (Test-Path -LiteralPath $uninstaller)) {
    throw "No se encontró el desinstalador. Instala XDM Companion primero."
  }
  & $uninstaller
  exit 0
}

if ($env:OS -ne "Windows_NT") {
  throw "Este instalador solo funciona en Windows PowerShell."
}
if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
  throw "Repositorio no válido: $Repository"
}

New-Item -ItemType Directory -Path $appDataDir -Force | Out-Null
try {
  Start-Transcript -LiteralPath $logFile -Append | Out-Null
  $transcriptStarted = $true
} catch { }

$workDir = Join-Path $env:TEMP ("xdm-companion-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null
try {
  Write-Info "Preparando la instalación de XDM Companion para Windows."
  $python = Ensure-Python
  Write-Info "Python: $python"

  if (-not (Get-SupportedRuntime)) {
    Install-LocalDeno $workDir
  }

  $release = Get-ValidatedRelease $Repository $Version
  Write-Info "Descargando XDM Companion $($release.Tag)."
  $archivePath = Join-Path $workDir "xdm-companion-windows.zip"
  $checksumPath = Join-Path $workDir "xdm-companion-windows.zip.sha256"
  Invoke-WebRequest -Uri $release.Archive -OutFile $archivePath
  Invoke-WebRequest -Uri $release.Checksum -OutFile $checksumPath
  $expectedHash = [regex]::Match((Get-Content -LiteralPath $checksumPath -Raw), "[0-9a-fA-F]{64}").Value
  $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
  if (-not $expectedHash -or $actualHash -ne $expectedHash) {
    throw "La verificación SHA-256 del paquete de XDM Companion falló."
  }

  $sourceDir = Join-Path $workDir "source"
  New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null
  Test-SafeZip $archivePath $sourceDir
  Expand-Archive -LiteralPath $archivePath -DestinationPath $sourceDir -Force
  foreach ($required in @("installer\install.py", "core\domains.json", "requirements.txt")) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDir $required) -PathType Leaf)) {
      throw "El paquete está incompleto; falta $required."
    }
  }

  $mainScript = Join-Path $companionDir "core\main.py"
  $wasRunning = Stop-RunningCompanion $mainScript
  try {
    & $python (Join-Path $sourceDir "installer\install.py")
    $installerExitCode = $LASTEXITCODE
    if ($installerExitCode -ne 0) {
      throw "El instalador de XDM Companion finalizó con código $installerExitCode."
    }
    $installedPython = Join-Path $companionDir "venv\Scripts\python.exe"
    $installedMain = Join-Path $companionDir "core\main.py"
    if (-not (Test-Path -LiteralPath $installedPython) -or -not (Test-Path -LiteralPath $installedMain)) {
      throw "La instalación terminó sin los archivos necesarios para iniciar el servicio."
    }
    Start-Companion $installedPython $installedMain (Split-Path -Parent $installedMain)
  } catch {
    if ($wasRunning -and (Test-Path -LiteralPath $mainScript)) {
      try {
        Start-Companion (Join-Path $companionDir "venv\Scripts\python.exe") `
          $mainScript (Split-Path -Parent $mainScript)
      } catch { }
    }
    throw
  }

  Write-Info "XDM Companion $($release.Tag) quedó instalado y activo."
  Write-Info "El servicio se iniciará automáticamente al iniciar sesión en Windows."
  Write-Info "Instala XDM Companion Bridge desde la tienda del navegador para habilitar la detección."
  Write-Info "Desinstalador: $appDataDir\uninstall.ps1"
  Write-Info "Registro del instalador: $logFile"
} finally {
  Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
  if ($transcriptStarted) {
    try { Stop-Transcript | Out-Null } catch { }
  }
}
