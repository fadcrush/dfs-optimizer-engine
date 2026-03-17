@echo off
REM refresh_injuries.bat
REM Fetches the latest NBA official injury report PDF and updates nba_news.duckdb.
REM Scheduled by Task Scheduler to run every 2 hours.
REM The NBA re-publishes the PDF every 15 minutes; we grab the newest one.

cd /d F:\Dev\N_B_A_and_N_F_L
call .venv\Scripts\activate.bat

python scripts\fetch_nba_injuries.py >> logs\injuries.log 2>&1
