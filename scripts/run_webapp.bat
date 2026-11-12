@echo off
rem QuantDex web console autostart (Task Scheduler "QuantDex Web", logon trigger, D-077).
rem Shared bootstrap: cd to repo root + resolve Python (see _bootstrap.bat).
rem ASCII-only on purpose: UTF-8 Chinese text in .bat files misparses on cp936
rem consoles (non-deterministic line merging; cost us a silent startup failure).
call "%~dp0_bootstrap.bat"
if errorlevel 1 exit /b 1
"%PY%" scripts\16_webapp.py >> data\store\logs\webapp.log 2>&1
