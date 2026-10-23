# Start (or restart) the journal watcher now and at every Windows logon, with no window.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

$python = (& python -c "import sys; print(sys.executable)").Trim()
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python" }

Push-Location $repo
try {
    & $python -m journal ping
    if ($LASTEXITCODE -ne 0) { throw 'Web app check failed. Fix config.json (python -m journal status) before installing.' }
} finally { Pop-Location }

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Deep Charts Journal.lnk'
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = $pythonw
$s.Arguments = '-m journal run'
$s.WorkingDirectory = $repo
$s.WindowStyle = 7
$s.Description = 'Deep Charts -> Google Sheets trade journal'
$s.Save()

# Stop a watcher that is already running, or the new one exits on its lock and old code keeps running.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match '-m journal run' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Stopped watcher (pid $($_.ProcessId))" }
Start-Sleep -Seconds 1

Start-Process -FilePath $pythonw -ArgumentList '-m', 'journal', 'run' -WorkingDirectory $repo -WindowStyle Hidden
Write-Host "Installed autostart: $lnk"
Write-Host "Watcher started. Log: $env:LOCALAPPDATA\DeepChartsJournal\journal.log"
