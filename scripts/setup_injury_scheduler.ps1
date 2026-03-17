# setup_injury_scheduler.ps1
# Registers a Windows Task Scheduler job that refreshes the NBA injury report
# every 2 hours from 8 AM to 8 PM (ET), starting today.
#
# Run once (as Administrator):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_injury_scheduler.ps1
#
# To remove the task later:
#   Unregister-ScheduledTask -TaskName "DFS_NBA_InjuryRefresh" -Confirm:$false

$TaskName = "DFS_NBA_InjuryRefresh"
$ScriptPath = "F:\Dev\N_B_A_and_N_F_L\scripts\refresh_injuries.bat"
$LogDir = "F:\Dev\N_B_A_and_N_F_L\logs"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Build a trigger that fires every 2 hours, 8 AM – 8 PM
$triggers = @()
foreach ($hour in 8, 10, 12, 14, 16, 18, 20) {
    $startTime = (Get-Date -Hour $hour -Minute 0 -Second 0).ToString("HH:mm")
    $triggers += New-ScheduledTaskTrigger -Daily -At $startTime
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$ScriptPath`""
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# Remove existing task if present, then register fresh
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task: $TaskName"
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $triggers `
    -Action $action `
    -Settings $settings `
    -Description "Refresh NBA injury report from official.nba.com every 2 hours (8AM-8PM)" `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host ""
Write-Host "Task '$TaskName' registered successfully."
Write-Host "Runs at: 8:00 AM, 10:00 AM, 12:00 PM, 2:00 PM, 4:00 PM, 6:00 PM, 8:00 PM"
Write-Host "Log:     $LogDir\injuries.log"
Write-Host ""
Write-Host "To run immediately: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "To remove task:     Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
