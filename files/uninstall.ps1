# Eosnox Labs - uninstaller
# Removes the program, its libraries and shortcuts. Laboratory data is KEPT unless you choose to delete it.
# Python 3.12 itself is left installed (remove it from Windows Settings > Apps if no longer needed).

$ErrorActionPreference = "Continue"
$Root    = Join-Path $env:LOCALAPPDATA "Programs\Eosnox Labs"
$DataDir = Join-Path $Root "data"

Write-Host "Eosnox Labs - uninstall" -ForegroundColor White
Write-Host "Program folder: $Root"
$sure = Read-Host "Remove Eosnox Labs from this PC? (Y/N)"
if ($sure -notmatch "^[Yy]") { Write-Host "Nothing was removed."; exit 0 }

$running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*launch_windows.pyw*" }
if ($running) {
    Write-Host "Close the Eosnox Labs window, then press Enter."
    Read-Host | Out-Null
}

Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath("Desktop")) "Eosnox Labs.lnk")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath("Programs")) "Eosnox Labs")
foreach ($part in @("app", "venv", "Help")) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root $part)
}
Write-Host "Program, libraries and shortcuts removed."

if (Test-Path $DataDir) {
    Write-Host ""
    Write-Host "Laboratory data (patients, results, reports) is in:" -ForegroundColor Yellow
    Write-Host "  $DataDir"
    $wipe = Read-Host "Type DELETE to erase it permanently, or press Enter to keep it"
    if ($wipe -ceq "DELETE") {
        Remove-Item -Recurse -Force $DataDir
        Write-Host "Laboratory data erased."
    } else {
        Write-Host "Laboratory data kept. Installing Eosnox Labs again will use it."
    }
}

if (-not (Test-Path $DataDir)) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Root "uninstall.ps1")
    Remove-Item -Force -ErrorAction SilentlyContinue $Root
}

$rule = Get-NetFirewallRule -DisplayName "Eosnox Labs - H33 results" -ErrorAction SilentlyContinue
if ($rule) {
    Write-Host "Removing the firewall rule (Windows will ask for permission)..."
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(
        "Remove-NetFirewallRule -DisplayName 'Eosnox Labs - H33 results'"))
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ErrorAction Stop `
            -ArgumentList "-NoProfile", "-EncodedCommand", $encoded | Out-Null
    } catch { Write-Host "Firewall rule left in place (permission not given)." }
}
Write-Host ""
Write-Host "Eosnox Labs has been uninstalled."
Read-Host "Press Enter to close"
