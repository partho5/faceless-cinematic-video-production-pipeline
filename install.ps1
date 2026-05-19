# Production bootstrap launcher - Windows (plan section 5).
#
# ONE job: guarantee a Python >= 3.10 exists (downloading + silently
# installing it, PATH-registered, if absent), then hand off to the brain
# install.py (stdlib-only) which does the real audit/install/verify.
# Order: existing Python  ->  winget  ->  signature-verified python.org
# installer (per-user, no admin).  All args forward verbatim to install.py.
#
# IMPORTANT: this file must be saved as UTF-8 *with BOM* and contain only
# ASCII characters in code. Windows PowerShell 5.1 (shipped on every
# Win10/11 by default) reads BOM-less files as the system ANSI codepage
# (cp1252 on most installs). Any non-ASCII byte then desyncs string
# parsing and the whole script fails to load. Keep this file ASCII-only.

$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$MinPy  = [Version]'3.10'
$PyVer  = '3.12.5'                       # pinned python.org fallback version
$LogFile = Join-Path $Root 'install.log'

function Write-Log($level, $msg) {
  $ts   = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  $line = "$ts [$level] [ps1] $msg`n"
  [System.IO.File]::AppendAllText($LogFile, $line, [System.Text.Encoding]::UTF8)
}

function Info($m){ Write-Host $m }
function Ok  ($m){ Write-Host $m -ForegroundColor Green }
function Warn($m){ Write-Host $m -ForegroundColor Yellow; Write-Log 'WARN'  $m }
function Err ($m){ Write-Host $m -ForegroundColor Red;    Write-Log 'ERROR' $m }

# True if $exe runs and is >= 3.10
function Test-Py($exe) {
  if (-not $exe) { return $false }
  try {
    $v = & $exe -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
    return ([Version]$v) -ge $MinPy
  } catch { return $false }
}

# Locate a usable Python: py launcher, PATH, registry, known per-user path,
# and our own previously-installed one.
function Find-Py {
  $cands = @()
  $pyl = (Get-Command py -ErrorAction SilentlyContinue)
  if ($pyl) {
    foreach ($t in '-3.13','-3.12','-3.11','-3.10','-3') {
      try { $p = & py $t -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p } } catch {}
    }
  }
  foreach ($n in 'python','python3') {
    $g = Get-Command $n -ErrorAction SilentlyContinue
    if ($g) { $cands += $g.Source }
  }
  foreach ($hive in 'HKCU:','HKLM:') {
    $base = "$hive\Software\Python\PythonCore"
    if (Test-Path $base) {
      Get-ChildItem $base | ForEach-Object {
        $ip = (Get-ItemProperty "$($_.PSPath)\InstallPath" -ErrorAction SilentlyContinue).'(default)'
        if ($ip) { $cands += (Join-Path $ip 'python.exe') }
      }
    }
  }
  $cands += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
  foreach ($c in $cands) {
    if ($c -and (Test-Path $c) -and (Test-Py $c)) { return $c }
  }
  return $null
}

function Install-Python {
  # 1) winget (MS-validated manifest; sets PATH)
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Info "Installing Python via winget (no admin needed)..."
    try {
      winget install -e --id Python.Python.3.12 --scope user --silent `
        --accept-source-agreements --accept-package-agreements | Out-Null
    } catch { Warn "winget path failed: $($_.Exception.Message)" }
    $p = Find-Py; if ($p) { return $p }
  }

  # 2) official python.org installer, Authenticode-verified, silent per-user
  $arch = if ($env:PROCESSOR_ARCHITECTURE -match 'ARM64') { 'arm64' } else { 'amd64' }
  $url  = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-$arch.exe"
  $exe  = Join-Path $env:TEMP "python-$PyVer-$arch.exe"
  Info ""
  Warn "No Python found. Downloading the official installer:"
  Info "  $url"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing

  # integrity: require a VALID Authenticode signature from the PSF
  $sig = Get-AuthenticodeSignature $exe
  $subj = $sig.SignerCertificate.Subject
  if ($sig.Status -ne 'Valid' -or $subj -notmatch 'Python Software Foundation') {
    Err "Installer signature not valid / not PSF ($($sig.Status)). Aborting."
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    return $null
  }
  Ok  "  signature verified (Python Software Foundation)"
  Info "Installing Python silently (per-user, PATH-registered, with Tk)..."
  $instArgs = '/quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 ' +
              'Include_pip=1 Include_launcher=1 SimpleInstall=1 ' +
              'SimpleInstallDescription="Video Production toolchain"'
  $p = Start-Process -FilePath $exe -ArgumentList $instArgs -Wait -PassThru
  Remove-Item $exe -Force -ErrorAction SilentlyContinue
  if ($p.ExitCode -ne 0) { Err "Python installer exit $($p.ExitCode)"; return $null }
  return (Find-Py)
}

# --- orchestrate ---------------------------------------------------------
Write-Log 'INFO' "install.ps1 start (ps_ver=$($PSVersionTable.PSVersion))"
Info "Video Production - bootstrap (Windows)"
$py = Find-Py
if (-not $py) {
  $py = Install-Python
  if (-not $py) {
    Err "Could not obtain Python automatically."
    Info "Install Python >= 3.10 from https://python.org (tick 'Add to PATH'),"
    Info "then re-run install.bat."
    exit 4
  }
  Ok  "Python ready: $py"
  Warn "PATH was updated for NEW terminals; this run continues with the"
  Warn "absolute path, so no action is needed now."
} else {
  Ok "Found Python: $py"
}

& $py (Join-Path $Root 'install.py') @args
$installExit = $LASTEXITCODE

if ($installExit -le 1) {
  $venvPythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
  $pngPath     = Join-Path $Root 'assets\icon.png'
  $icoPath     = Join-Path $Root 'assets\icon.ico'
  $lnkPath     = Join-Path $Root 'Video Production Studio.lnk'

  if ((Test-Path $venvPythonw) -and (Test-Path $pngPath)) {
    if (-not (Test-Path $icoPath)) {
      & $venvPythonw -c "from PIL import Image; Image.open(r'$pngPath').save(r'$icoPath')" 2>$null
    }
    if ((Test-Path $icoPath) -and -not (Test-Path $lnkPath)) {
      try {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnkPath)
        $sc.TargetPath      = $venvPythonw
        $sc.Arguments       = "`"$Root\run.py`""
        $sc.IconLocation    = $icoPath
        $sc.WorkingDirectory = $Root
        $sc.Save()
        Ok "Launcher created: Video Production Studio.lnk"
      } catch {
        Warn "Could not create shortcut: $($_.Exception.Message)"
      }
    }
    if (Test-Path $lnkPath) {
      try {
        $desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Video Production Studio.lnk'
        Copy-Item $lnkPath $desktopLnk -Force
        Ok "Desktop shortcut created: $desktopLnk"
      } catch {
        Warn "Could not copy shortcut to Desktop: $($_.Exception.Message)"
      }
    }
  }
}

Write-Log 'INFO' "install.ps1 exit $installExit"
exit $installExit
