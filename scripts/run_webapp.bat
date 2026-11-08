@echo off
rem QuantDex 控制台自启动（任务计划 "QuantDex Web"，登录时触发，D-077）
cd /d D:\MeineArbeit\QuantDex
D:\conda\miniconda3\envs\QuantDex\python.exe scripts\16_webapp.py >> data\store\logs\webapp.log 2>&1
