# Register scheduled tasks for Peace Paths auto-updates
# Runs from project root, using the production pipeline

$projectRoot = "C:\Users\Erez\.pi\agent\projects\peace-paths"
$python = "C:\ProgramData\anaconda3\python.exe"

# ── Fast update (every hour, no end) ──
$actionFast = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "ai-analyze-prod.py --fast" `
    -WorkingDirectory $projectRoot

$triggerFast = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 1)

$settingsFast = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName "PeacePaths-FastUpdate" `
    -Action $actionFast `
    -Trigger $triggerFast `
    -Settings $settingsFast `
    -Description "Hourly fast AI analysis (last 2h) + KV upload" `
    -User $env:USERNAME `
    -Force

# ── Daily update (2:00 AM) ──
$actionDaily = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "ai-analyze-prod.py --daily" `
    -WorkingDirectory $projectRoot

$triggerDaily = New-ScheduledTaskTrigger `
    -Daily `
    -At "2:00AM"

$settingsDaily = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName "PeacePaths-DailyUpdate" `
    -Action $actionDaily `
    -Trigger $triggerDaily `
    -Settings $settingsDaily `
    -Description "Daily full AI analysis (7-day window) + KV upload" `
    -User $env:USERNAME `
    -Force

# ── Remove old task (best effort) ──
try {
    Unregister-ScheduledTask -TaskName "PeaceRoom-AutoDeploy" -Confirm:$false
    Write-Host "Removed old task: PeaceRoom-AutoDeploy"
} catch {
    Write-Host "Old task not found or permission denied (ok)"
}

Write-Host "`nTasks registered:"
Get-ScheduledTask | Where-Object {$_.TaskName -match 'PeacePaths'} | Format-Table TaskName, State
Write-Host "`nNext run times:"
Get-ScheduledTaskInfo -TaskName 'PeacePaths-FastUpdate' | Select-Object -ExpandProperty NextRunTime
Get-ScheduledTaskInfo -TaskName 'PeacePaths-DailyUpdate' | Select-Object -ExpandProperty NextRunTime
