# Remove old task (best effort)
try {
    Unregister-ScheduledTask -TaskName "PeaceRoom-AutoDeploy" -Confirm:$false
    Write-Host "Removed: PeaceRoom-AutoDeploy"
} catch {
    Write-Host "Could not remove old task (may need admin): PeaceRoom-AutoDeploy"
}

# Fast hourly update — runs every 1 hour, repeats for 30 days
$fastAction = New-ScheduledTaskAction `
    -Execute "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-fast-update.bat" `
    -WorkingDirectory "C:\Users\Erez\.pi\agent\projects\peace-paths"

$fastTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 30)

$fastSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 0

Register-ScheduledTask `
    -TaskName "PeacePaths-FastUpdate" `
    -Action $fastAction `
    -Trigger $fastTrigger `
    -Settings $fastSettings `
    -Description "Peace Paths: Fast hourly AI update (last 2h window, upload to KV)"

Write-Host "Created: PeacePaths-FastUpdate (hourly)"

# Daily full update — runs at 2 AM each day
$dailyAction = New-ScheduledTaskAction `
    -Execute "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-daily-update.bat" `
    -WorkingDirectory "C:\Users\Erez\.pi\agent\projects\peace-paths"

$dailyTrigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "2:00AM"

$dailySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 0

Register-ScheduledTask `
    -TaskName "PeacePaths-DailyUpdate" `
    -Action $dailyAction `
    -Trigger $dailyTrigger `
    -Settings $dailySettings `
    -Description "Peace Paths: Daily full AI update (7-day window, upload to KV)"

Write-Host "Created: PeacePaths-DailyUpdate (daily at 2 AM)"
