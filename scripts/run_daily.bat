@echo off
cd /d D:\MeineArbeit\QuantDex
if not exist data\store\logs mkdir data\store\logs
rem 中文日志统一 UTF-8（否则写入 UTF-8 日志文件会乱码；2026-10-26 修）
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"D:\conda\miniconda3\envs\QuantDex\python.exe" scripts\02b_update_pit.py >> data\store\logs\daily_run.log 2>&1
"D:\conda\miniconda3\envs\QuantDex\python.exe" scripts\10_daily_run.py --skip-update >> data\store\logs\daily_run.log 2>&1
exit /b %ERRORLEVEL%
