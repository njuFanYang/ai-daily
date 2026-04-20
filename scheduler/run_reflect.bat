@echo off
REM 双周触发：让 Claude 反思并修正权重
cd /d E:\Develop\ai-daily\server
python claude_reflect.py >> logs\cron.log 2>&1
