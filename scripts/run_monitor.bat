@echo off
rem QuantDex intraday monitor one-click (2026-11-18). Double-click in the morning;
rem it waits for 09:20 automatically, watches until 15:05, then writes a summary.
rem ASCII-only on purpose: UTF-8 Chinese text in .bat files misparses on cp936.
call "%~dp0_bootstrap.bat"
if errorlevel 1 exit /b 1
"%PY%" scripts\intraday_monitor.py
pause
