@echo off
REM Daily projection generation script
REM Schedule this with Windows Task Scheduler to run daily at 8am

echo ========================================
echo   DAILY DFS PROJECTION GENERATION
echo ========================================
echo.

REM Navigate to project
cd C:\Users\David\Documents\N_B_A_and_N_F_L\backend

REM Activate virtual environment
call venv\Scripts\activate

REM Run projections
python manage.py run_projections --save-to-db --validate-previous

REM Log result
echo.
echo ========================================
echo   PROJECTION GENERATION COMPLETE
echo   Time: %date% %time%
echo ========================================

pause