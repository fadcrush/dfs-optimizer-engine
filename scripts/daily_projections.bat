@echo off
REM Daily projection generation script
REM Schedule this with Windows Task Scheduler to run daily at 8am

echo ========================================
echo   DAILY DFS PROJECTION GENERATION
echo ========================================
echo.

REM Navigate to project root
cd F:\Dev\N_B_A_and_N_F_L

REM Activate virtual environment
call .venv\Scripts\activate

REM Step 1: Catch-up game logs (last 2 days to catch late-posted box scores)
echo [1/3] Refreshing game logs...
python scripts\ingest_game_logs.py --days 2
echo.

REM Step 2: Fetch today's NBA official injury report (updates nba_news.duckdb)
REM   Source: https://official.nba.com/nba-injury-report-2025-26-season/
REM   OUT players are automatically removed from the player pool.
echo [2/3] Fetching NBA injury report...
python scripts\fetch_nba_injuries.py
echo.

REM Step 3: Run projections
echo [3/3] Generating projections...
python scripts\run_dfs_pipeline.py

REM Log result
echo.
echo ========================================
echo   PROJECTION GENERATION COMPLETE
echo   Time: %date% %time%
echo ========================================

pause