# Stop the journal watcher and remove its autostart shortcut. Local state and config are kept
# unless -Purge is given (the Google Sheet is never touched).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 [-Purge]
param([switch]$Purge)

Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match '-m journal run' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped watcher (pid $($_.ProcessId))" }

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Deep Charts Journal.lnk'
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Removed $lnk" }

if ($Purge) {
    $dir = Join-Path $env:LOCALAPPDATA 'DeepChartsJournal'
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force; Write-Host "Deleted $dir" }
}
