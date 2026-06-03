@echo off
REM Daily full update — AI analysis (7-day window) + Cloudflare KV upload
REM Runs via Windows Task Scheduler: PeacePaths-DailyUpdate (once per day)
echo [%DATE% %TIME%] START daily-update >> "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-daily.log" 2>&1
cd /d "C:\Users\Erez\.pi\agent\projects\peace-paths\dev-environment"
set PYTHONIOENCODING=utf-8
C:\ProgramData\anaconda3\python.exe ai-analyze-prod.py --daily >> "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-daily.log" 2>&1
echo [%DATE% %TIME%] EXIT: %ERRORLEVEL% >> "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-daily.log" 2>&1
