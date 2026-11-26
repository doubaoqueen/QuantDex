@echo off
rem Task Scheduler "QuantDex Daily" wrapper, three stages (D-090/D-101):
rem   1) 02b --skip-minute   daily bars + indexes (fast)
rem   2) 10 --skip-update   four shadow accounts + combined mail  <-- mail goes out here
rem   3) 02b --minute-only  5-minute bars (10-40 min; off the mail critical path)
rem D-101: every stage now logs a dated header - the old undated log made it
rem impossible to tell whether the 19:00 task even fired on a missed day.
rem ASCII-only on purpose: UTF-8 Chinese text in .bat files misparses on cp936
rem consoles (non-deterministic line merging; cost us a silent startup failure).
call "%~dp0_bootstrap.bat"
if errorlevel 1 exit /b 1
rem Unified UTF-8 logs (Chinese would garble otherwise; fixed 2026-10-26)
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
rem ===== D-101: dated stage headers (grep "STAGE" to audit when runs happened) =====
echo ===== STAGE 1/3 daily-bars start %date% %time% >> data\store\logs\daily_run.log
"%PY%" scripts\02b_update_pit.py --skip-minute >> data\store\logs\daily_run.log 2>&1
set RC_DATA=%ERRORLEVEL%
echo ===== STAGE 2/3 pipeline+mail start %date% %time% RC_DATA=%RC_DATA% >> data\store\logs\daily_run.log
"%PY%" scripts\10_daily_run.py --skip-update >> data\store\logs\daily_run.log 2>&1
set RC_MAIL=%ERRORLEVEL%
echo ===== STAGE 3/3 minute-bars start %date% %time% RC_MAIL=%RC_MAIL% >> data\store\logs\daily_run.log
rem stage 3: 5-minute bars; failure here must NOT mask a successful mail run
"%PY%" scripts\02b_update_pit.py --minute-only >> data\store\logs\daily_run.log 2>&1
echo ===== STAGE 3/3 minute-bars done %date% %time% RC_MINUTE=%ERRORLEVEL% >> data\store\logs\daily_run.log
if not "%RC_MAIL%"=="0" exit /b %RC_MAIL%
if not "%RC_DATA%"=="0" exit /b %RC_DATA%
exit /b 0
