# Registers a Task Scheduler task that starts the collector when you log on
# and restarts it if it stops. Run from the repo folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows-task.ps1

param(
    [string]$TaskName = "Solar collector",
    [string]$RepoDir = (Resolve-Path "$PSScriptRoot\..").Path
)

$pythonw = Join-Path $RepoDir ".venv\Scripts\pythonw.exe"
$config = Join-Path $RepoDir "config.toml"
if (-not (Test-Path $pythonw)) { throw "No virtualenv at $pythonw. See README setup steps." }
if (-not (Test-Path $config)) { throw "No config at $config. Copy config.example.toml first." }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m solar_monitoring.cli --config `"$config`" run" `
    -WorkingDirectory $RepoDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Polls the Enphase IQ Gateway into solar.db" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered and started '$TaskName'. Log: $(Join-Path $RepoDir 'solar.log')"
