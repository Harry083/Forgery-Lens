<#
.SYNOPSIS
  Build the Clarity Windows installer: dist\installer\Clarity-<version>-Setup.exe

.DESCRIPTION
  1. Creates a clean virtual environment (.build-venv) and installs the requirements and PyInstaller.
     With -RebuildBootloader, PyInstaller's launcher is compiled from source on this machine (needs the Visual
     Studio C++ build tools). A locally built launcher doesn't share the byte pattern of the stock one that
     some malware also uses, which removes the most common reason for antivirus false positives.
  2. Runs the tests, then builds the one-folder app (dist\Clarity\) and checks it with Clarity.exe --self-test.
  3. Signs Clarity.exe and the installer when a code-signing certificate is configured (see below).
  4. Compiles packaging\Clarity.iss with Inno Setup 6 (installed with winget or Chocolatey if missing).

  Code signing (optional, but it's what removes SmartScreen's "unknown publisher" warning). Set one of:
    CLARITY_SIGN_THUMBPRINT              thumbprint of a certificate in the current user's or machine's store
    CLARITY_SIGN_PFX / CLARITY_SIGN_PASSWORD   path to a .pfx file and its password
    CLARITY_SIGN_PFX_BASE64 / CLARITY_SIGN_PASSWORD   the .pfx as base64 (for CI secrets)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1 -RebuildBootloader
#>
param(
  [switch]$RebuildBootloader,
  [switch]$SkipTests,
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Run($exe) {
  & $exe @args
  if ($LASTEXITCODE -ne 0) { throw "$exe $args failed with exit code $LASTEXITCODE" }
}

$version = (Select-String -Path backend\__init__.py -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Clarity $version"

# ---------------------------------------------------------------- environment
Step "Creating the build environment"
if (Test-Path .build-venv) { Remove-Item -Recurse -Force .build-venv }
Run $Python -m venv .build-venv
$py = Join-Path $root ".build-venv\Scripts\python.exe"
Run $py -m pip install --upgrade pip wheel
Run $py -m pip install -r requirements.txt pytest
$pyinstallerVersion = "6.22.3"  # pinned so builds are repeatable; raise deliberately
$started = Get-Date
if ($RebuildBootloader) {
  # Compile PyInstaller's launcher (the "bootloader") from its source with this machine's C compiler, as
  # PyInstaller's documentation describes, then install PyInstaller from that source tree.
  Step "Compiling PyInstaller's launcher from source"
  $src = Join-Path $env:TEMP "clarity-pyinstaller-src"
  if (Test-Path $src) { Remove-Item -Recurse -Force $src }
  New-Item -ItemType Directory $src | Out-Null
  Run $py -m pip download --no-binary :all: --no-deps "pyinstaller==$pyinstallerVersion" -d $src
  $archive = Get-ChildItem $src -Filter "pyinstaller-*.tar.gz" | Select-Object -First 1
  # Unpack with Python, not tar.exe: the archive holds a few symbolic links (changelog notes, test data) that
  # Windows' own tar can't create without Developer Mode, and the build doesn't need them.
  $unpack = Join-Path $src "unpack.py"
  Set-Content -Encoding utf8 $unpack @'
import sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    members = [m for m in archive.getmembers() if not (m.issym() or m.islnk())]
    extra = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    archive.extractall(sys.argv[2], members=members, **extra)
'@
  Run $py $unpack $archive.FullName $src
  $tree = Join-Path $src "pyinstaller-$pyinstallerVersion"
  Push-Location (Join-Path $tree "bootloader")
  try { Run $py ./waf all --target-arch=64bit } finally { Pop-Location }
  Run $py -m pip install $tree
} else {
  Run $py -m pip install "pyinstaller==$pyinstallerVersion"
}

if (-not $SkipTests) {
  Step "Running the tests"
  Run $py -m pytest tests -q -p no:cacheprovider
}

# ---------------------------------------------------------------- app
Step "Building the app (one-folder)"
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (Test-Path build) { Remove-Item -Recurse -Force build }
Run $py -m PyInstaller --clean --noconfirm Clarity.spec
$exe = Join-Path $root "dist\Clarity\Clarity.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller didn't produce $exe" }
$launcher = & $py -c "import PyInstaller, os; print(os.path.join(os.path.dirname(PyInstaller.__file__), 'bootloader', 'Windows-64bit-intel', 'runw.exe'))"
$launcherInfo = Get-Item $launcher
Write-Host "Launcher: $launcher"
Write-Host "  built $($launcherInfo.LastWriteTime)  SHA-256 $((Get-FileHash $launcher -Algorithm SHA256).Hash.ToLower())"
if ($RebuildBootloader -and $launcherInfo.LastWriteTime -lt $started) {
  throw "The launcher wasn't rebuilt on this machine (it predates this build)"
}

Step "Self-test of the packaged app"
$report = Join-Path $env:TEMP "clarity-self-test.json"
if (Test-Path $report) { Remove-Item $report }
$proc = Start-Process -FilePath $exe -ArgumentList "--self-test", "`"$report`"" -Wait -PassThru
if (Test-Path $report) { Get-Content $report | Write-Host }
if ($proc.ExitCode -ne 0) { throw "The packaged app failed its self-test (exit code $($proc.ExitCode))" }

# ---------------------------------------------------------------- signing
function Find-SignTool {
  $found = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
  if ($found) { return $found.FullName }
  $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

$signArgs = $null
if ($env:CLARITY_SIGN_PFX_BASE64) {
  $env:CLARITY_SIGN_PFX = Join-Path $env:TEMP "clarity-signing.pfx"
  [IO.File]::WriteAllBytes($env:CLARITY_SIGN_PFX, [Convert]::FromBase64String($env:CLARITY_SIGN_PFX_BASE64))
}
if ($env:CLARITY_SIGN_THUMBPRINT) {
  $signArgs = @("/sha1", $env:CLARITY_SIGN_THUMBPRINT)
} elseif ($env:CLARITY_SIGN_PFX) {
  $signArgs = @("/f", $env:CLARITY_SIGN_PFX, "/p", $env:CLARITY_SIGN_PASSWORD)
}
$signtool = $null
if ($signArgs) {
  $signtool = Find-SignTool
  if (-not $signtool) { throw "A signing certificate is configured but signtool.exe (Windows SDK) wasn't found" }
  $signArgs = @("sign", "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256",
                "/d", "Clarity") + $signArgs
  Step "Signing Clarity.exe"
  Run $signtool @signArgs $exe
} else {
  Write-Host "`nNo code-signing certificate configured: building unsigned (SmartScreen may warn on first run)." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- installer
Step "Compiling the installer"
function Find-Iscc {
  foreach ($p in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                   "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
    if (Test-Path $p) { return $p }
  }
  $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}
$iscc = Find-Iscc
if (-not $iscc) {
  Write-Host "Inno Setup 6 not found; installing it"
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements | Out-Host
  } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
    choco install innosetup -y --no-progress | Out-Host
  }
  $iscc = Find-Iscc
  if (-not $iscc) { throw "Inno Setup 6 is needed: install it from https://jrsoftware.org/isdl.php and run this again" }
}
$isccArgs = @("/DAppVersion=$version")
if ($signArgs) {
  # Inno Setup signs the installer and its uninstaller itself, through this named sign tool
  $quoted = ($signArgs | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join " "
  $isccArgs += @("/DSign", "/Ssigntool=`"$signtool`" $quoted `$f")
}
Run $iscc @isccArgs packaging\Clarity.iss

$setup = Join-Path $root "dist\installer\Clarity-$version-Setup.exe"
if (-not (Test-Path $setup)) { throw "Inno Setup didn't produce $setup" }
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  Clarity-$version-Setup.exe" | Set-Content -Encoding ascii (Join-Path $root "dist\installer\SHA256SUMS.txt")
Step "Done"
Write-Host "Installer: $setup"
Write-Host "SHA-256:   $hash"
