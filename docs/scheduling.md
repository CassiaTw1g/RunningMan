# Scheduling a randomised daily run

The composer itself is a one-shot CLI. To make it fire once a day at an unpredictable moment,
wrap it in a scheduler that sleeps until a randomly chosen slot.

## Slot policy

- 50/50 between a morning window and an afternoon window (defaults `09:00–10:00` / `14:00–15:00`)
- uniform minute inside the chosen window
- one record per day, guaranteed by a history check before dispatch
- if the machine was off at the slot, the run happens late instead of being lost

## Windows (Task Scheduler)

```powershell
$py     = "C:\Path\To\pythonw.exe"
$script = "D:\path\to\scripts\daily_random_run.py"
$action  = New-ScheduledTaskAction -Execute $py -Argument ('"' + $script + '"') -WorkingDirectory (Split-Path $script)
$trigger = New-ScheduledTaskTrigger -Daily -At 08:45
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
       -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Daily Random Run" -Action $action -Trigger $trigger -Settings $set -Force
```

The 08:45 trigger only *starts* the wrapper; the wrapper computes the real slot and sleeps until it,
so the visible upload time is inside the window, not at 08:45.

## Linux / macOS (cron)

```
45 8 * * *  /usr/bin/python3 /path/to/daily_random_run.py >> /var/log/run.log 2>&1
```

Same behaviour: the cron entry starts the wrapper, the wrapper owns the randomised delay.

## Configuration

`scripts/config.json` (kept outside the repository, contains the endpoint and token path):

```json
{
  "base": "https://<host>",
  "token_file": "../work/token.txt",
  "repo": "/abs/path/to/RunningMan",
  "window_am": ["09:00", "10:00"],
  "window_pm": ["14:00", "15:00"]
}
```

## Manual checks

```bash
python daily_random_run.py --dry-run     # what would happen today
python daily_random_run.py --now         # run immediately (respects the daily guard)
python daily_random_run.py --now --force # bypass the guard
```

Logs land in `scripts/logs/run-YYYYMMDD.log`, one line per event plus the tail of the child process.

## Failure modes

| Symptom | Meaning |
|---|---|
| `a record already exists ... - skip` | daily guard tripped, nothing to do |
| `errno 50014` in the child output | token expired — refresh it and re-run |
| no log file for the day | the machine was off and the task never started |
