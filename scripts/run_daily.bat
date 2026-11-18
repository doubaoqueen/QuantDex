@echo off
rem Task Scheduler "QuantDex Daily" wrapper: bootstrap (cd to repo root + resolve
rem Python via _bootstrap.bat), then three stages (D-090):
rem   1) 02b --skip-minute   daily bars + indexes (fast)
rem   2) 10 --skip-update   four shadow accounts + combined mail  <-- mail goes out here
rem   3) 02b --minute-only  5-minute bars (10-40 min; off the mail critical path)
rem ASCII-only on purpose: UTF-8 Chinese text in .bat files misparses on cp936
rem consoles (non-deterministic line merging; cost us a silent startup failure).
call "%~dp0_bootstrap.bat"
if errorlevel 1 exit /b 1
rem Unified UTF-8 logs (Chinese would garble otherwise; fixed 2026-10-26)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
rem stage 1: daily data (minute bars deferred to stage 3)
"%PY%" scripts\02b_update_pit.py --skip-minute >> data\store\logs\daily_run.log 2>&1
set RC_DATA=%ERRORLEVEL%
rem stage 2: pipeline + mail (exit code of this stage is what the task reports)
"%PY%" scripts\10_daily_run.py --skip-update >> data\store\logs\daily_run.log 2>&1
set RC_MAIL=%ERRORLEVEL%
rem stage 3: 5-minute bars; failure here must NOT mask a successful mail run
"%PY%" scripts\02b_update_pit.py --minute-only >> data\store\logs\daily_run.log 2>&1
if not "%RC_MAIL%"=="0" exit /b %RC_MAIL%
if not "%RC_DATA%"=="0" exit /b %RC_DATA%
exit /b 0
