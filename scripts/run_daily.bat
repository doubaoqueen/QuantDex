@echo off
cd /d D:\MeineArbeit\QuantDex
if not exist data\store\logs mkdir data\store\logs
"D:\conda\miniconda3\envs\QuantDex\python.exe" scripts\10_daily_run.py >> data\store\logs\daily_run.log 2>&1
