@echo off
REM Fast hourly update — AI analysis (last 2h) + Cloudflare KV upload
REM Runs via Windows Task Scheduler: PeacePaths-FastUpdate (every 1 hour)
echo [%DATE% %TIME%] START fast-update >> "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-fast.log" 2>&1
cd /d "C:\Users\Erez\.pi\agent\projects\peace-paths"
set PYTHONIOENCODING=utf-8
C:\ProgramData\anaconda3\python.exe ai-analyze-prod.py --fast >> "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-fast.log" 2>&1
echo [%DATE% %TIME%] EXIT: %ERRORLEVEL% >> "C:\Users\Erez\.pi\agent\projects\peace-paths\auto-fast.log" 2>&1
