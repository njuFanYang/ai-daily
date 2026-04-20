@echo off
REM 每日 8:00 触发：跑完整 pipeline
cd /d E:\Develop\ai-daily\server
python pipeline.py >> logs\cron.log 2>&1
