# Eosnox Labs - Windows installer
# Started by "Install Eosnox Labs.bat". Works offline: Python and every library are in this folder.
# Installs for the current Windows user into %LOCALAPPDATA%\Programs\Eosnox Labs.
# Reinstalling (or installing a newer version) keeps the laboratory data.

# Windows PowerShell 5.1 turns a program's warning text into a fatal error under "Stop",
# so errors are checked step by step instead.
$ErrorActionPreference = "Continue"
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root     = Join-Path $env:LOCALAPPDATA "Programs\Eosnox Labs"
$AppDir   = Join-Path $Root "app"
$VenvDir  = Join-Path $Root "venv"
$DataDir  = Join-Path $Root "data"
$H33Port  = 8001

function Step($text)  { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)    { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "    !   $text" -ForegroundColor Yellow }
function Fail($text)  { Write-Host ""; Write-Host "INSTALL STOPPED: $text" -ForegroundColor Red; exit 1 }
function Invoke-Elevated($script) {
    # Runs a PowerShell script with administrator rights (Windows asks once). Returns its exit code,
    # or -1 if permission was refused. The script is passed encoded, so no quoting can break it.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    try {
        $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
            -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded -ErrorAction Stop
        return $p.ExitCode
    } catch { return -1 }
}

Write-Host "Eosnox Labs - installer" -ForegroundColor White
Write-Host "Installs to: $Root"

# ------------------------------------------------------------------ 1. Windows
Step "Checking Windows"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "Eosnox Labs needs 64-bit Windows 10 or 11." }
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 17763) { Fail "Eosnox Labs needs Windows 10 (version 1809 or newer) or Windows 11." }
Ok "64-bit Windows, build $build"

# ------------------------------------------------------------------ 2. Close a running copy
$running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*launch_windows.pyw*" }
if ($running) {
    Step "Eosnox Labs is open"
    Write-Host "    Close the Eosnox Labs window, then press Enter to continue."
    Read-Host | Out-Null
    $running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*launch_windows.pyw*" }
    if ($running) { Fail "Eosnox Labs is still open. Close it and run the installer again." }
}

# ------------------------------------------------------------------ 3. Python 3.12
Step "Checking Python 3.12"
function Find-Python {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $found = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
    }
    return $null
}
$Python = Find-Python
if (-not $Python) {
    $installer = Get-ChildItem (Join-Path $Here "python") -Filter "python-3.12*-amd64.exe" | Select-Object -First 1
    if (-not $installer) { Fail "The Python installer is missing from the 'files\python' folder." }
    Write-Host "    Installing Python 3.12 for this user (about 1 minute)..."
    $p = Start-Process -FilePath $installer.FullName -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=1", "InstallLauncherAllUsers=0",
        "Include_test=0", "Include_doc=0", "Include_tcltk=0", "Shortcuts=0")
    if ($p.ExitCode -ne 0) { Fail "Python setup ended with code $($p.ExitCode)." }
    $Python = Find-Python
    if (-not $Python) { Fail "Python 3.12 was installed but could not be found." }
}
Ok "Python: $Python"

# ------------------------------------------------------------------ 4. Program files
Step "Copying Eosnox Labs"
try {
    New-Item -ItemType Directory -Force -Path $Root, $DataDir -ErrorAction Stop | Out-Null
    if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir -ErrorAction Stop }
    Copy-Item -Recurse (Join-Path $Here "app") $AppDir -ErrorAction Stop
    Copy-Item (Join-Path $Here "uninstall.ps1") (Join-Path $Root "uninstall.ps1") -Force -ErrorAction Stop
} catch { Fail "Could not copy the program files: $($_.Exception.Message)" }
Ok "Program: $AppDir"
if (Test-Path (Join-Path $DataDir "vetlab.db")) { Ok "Existing laboratory data kept: $DataDir" }
else { Ok "Laboratory data will be created on first start: $DataDir" }

# ------------------------------------------------------------------ 5. Libraries (offline)
Step "Installing libraries (offline)"
$venvPython = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython -c "import sys" 2>$null
    if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $VenvDir }
}
if (-not (Test-Path $venvPython)) {
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment in $VenvDir." }
}
& $venvPython -m pip install --quiet --disable-pip-version-check --no-index `
    --find-links (Join-Path $Here "wheels") -r (Join-Path $Here "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { Fail "Installing the libraries failed (see the messages above)." }
& $venvPython -c "import flask, webview, paramiko, psutil, keyring" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "The libraries were installed but do not load." }
Ok "Libraries installed"

# ------------------------------------------------------------------ 6. Shortcuts
Step "Creating shortcuts"
$pythonw = Join-Path $VenvDir "Scripts\pythonw.exe"
$launcher = Join-Path $AppDir "launch_windows.pyw"
$icon = Join-Path $AppDir "static\eosnox.ico"
$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Eosnox Labs"
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Eosnox Labs.lnk"),
    (Join-Path $startMenu "Eosnox Labs.lnk")
)
foreach ($lnkPath in $targets) {
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "`"$launcher`""
    $lnk.WorkingDirectory = $AppDir
    $lnk.Description = "Eosnox Labs laboratory software"
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Save()
}
$un = $shell.CreateShortcut((Join-Path $startMenu "Uninstall Eosnox Labs.lnk"))
$un.TargetPath = "powershell.exe"
$un.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Root 'uninstall.ps1')`""
$un.Save()
$help = Join-Path $Here "..\Help"
if (Test-Path $help) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "Help") | Out-Null
    Copy-Item -Recurse -Force (Join-Path $help "*") (Join-Path $Root "Help")
    $guide = Get-ChildItem (Join-Path $Root "Help") -Filter "*.html" | Select-Object -First 1
    if ($guide) {
        $g = $shell.CreateShortcut((Join-Path $startMenu "Eosnox Labs Guide.lnk"))
        $g.TargetPath = $guide.FullName
        $g.Save()
    }
}
Ok "Desktop and Start menu shortcuts"

# ------------------------------------------------------------------ 7. WebView2 (the window engine)
Step "Checking Microsoft Edge WebView2"
$wv = $false
foreach ($key in @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")) {
    $v = (Get-ItemProperty -Path $key -Name pv -ErrorAction SilentlyContinue).pv
    if ($v -and $v -ne "0.0.0.0") { $wv = $true }
}
if ($wv) { Ok "WebView2 is installed" }
else {
    Warn "Microsoft Edge WebView2 Runtime was not found. Eosnox Labs needs it to show its window."
    Warn "Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 (free, from Microsoft)."
}

# ------------------------------------------------------------------ 8. Firewall (needs administrator)
Step "Allowing the H33 to send results (Windows Firewall)"
Write-Host "    Windows will ask for permission. This lets the H33 reach this PC on port $H33Port."
$rule = "New-NetFirewallRule -DisplayName 'Eosnox Labs - H33 results' -Group 'Eosnox Labs' -Direction Inbound " +
        "-Protocol TCP -LocalPort $H33Port -Action Allow -Profile Any | Out-Null; " +
        "if (Get-NetFirewallRule -DisplayName 'Eosnox Labs - H33 results' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
$existing = Get-NetFirewallRule -DisplayName "Eosnox Labs - H33 results" -ErrorAction SilentlyContinue
if ($existing) { Ok "Firewall rule already present" }
else {
    $code = Invoke-Elevated $rule
    if ($code -eq 0) { Ok "Firewall rule added (TCP $H33Port in)" }
    elseif ($code -eq -1) { Warn "Permission was not given, so the firewall rule was not added. Run the installer again to add it." }
    else { Warn "Firewall rule not added. If H33 results do not arrive, see 'Troubleshooting' in the guide." }
}

# ------------------------------------------------------------------ Done
Write-Host ""
Write-Host "Eosnox Labs is installed." -ForegroundColor Green
Write-Host "  Start it from the 'Eosnox Labs' icon on the Desktop or in the Start menu."
Write-Host "  First sign-in: user admin, password admin (change it under Settings)."
Write-Host "  Laboratory data: $DataDir"
Write-Host ""
$answer = Read-Host "Open Eosnox Labs now? (Y/N)"
if ($answer -match "^[Yy]") { Start-Process -FilePath $pythonw -ArgumentList "`"$launcher`"" -WorkingDirectory $AppDir }
