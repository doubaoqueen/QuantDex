@echo off
rem Task Scheduler "QuantDex Daily" wrapper: bootstrap (cd to repo root + resolve
rem Python via _bootstrap.bat), then 02b (market refresh) -> 10 (pipeline + mail).
rem ASCII-only on purpose: UTF-8 Chinese text in .bat files misparses on cp936
rem consoles (non-deterministic line merging; cost us a silent startup failure).
call "%~dp0_bootstrap.bat"
if errorlevel 1 exit /b 1
rem Unified UTF-8 logs (Chinese would garble otherwise; fixed 2026-10-26)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"%PY%" scripts\02b_update_pit.py >> data\store\logs\daily_run.log 2>&1
"%PY%" scripts\10_daily_run.py --skip-update >> data\store\logs\daily_run.log 2>&1
exit /b %ERRORLEVEL%
