@echo off
REM Wrapper for Windows Task Scheduler.
REM Adjust the path to python.exe / venv below if needed.

cd /d "%~dp0"
python poster.py >> logs\task_scheduler_stdout.log 2>&1
