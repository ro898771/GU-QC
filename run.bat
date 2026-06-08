@echo off
setlocal

:: ============================================================
:: run.bat
:: 1) Runs main.py to extract & concatenate GU result files.
:: 2) Runs plot_failures.py to generate Plotly HTML charts.
::
:: Usage:
::   Double-click              -> processes GUFILE folder (default)
::   run.bat GUFILE-old        -> processes GUFILE-old
::   run.bat GUFILE GUFILE-old -> processes both in sequence
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%src\main.py"
set "PLOT_SCRIPT=%SCRIPT_DIR%src\plot_failures.py"
set "ENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

:: Prefer the project venv python; fall back to system py
if exist "%ENV_PYTHON%" (
    set "PY=%ENV_PYTHON%"
) else (
    set "PY=py"
)

:: If no arguments supplied, default to GUFILE
if "%~1"=="" (
    set "FOLDERS=GUFILE"
) else (
    set "FOLDERS=%*"
)

echo ============================================================
echo  GU File Processor
echo  Python : %PY%
echo  Script : %SCRIPT%
echo  Folder(s): %FOLDERS%
echo ============================================================
echo.

:: ── Step 1: Process each folder ─────────────────────────────────────────────────
for %%F in (%FOLDERS%) do (
    echo [START] Processing folder: %%F
    echo.
    "%PY%" "%SCRIPT%"
    if errorlevel 1 (
        echo.
        echo [ERROR] Processing failed for folder: %%F
        echo.
    ) else (
        echo.
        echo [DONE]  Output written to: %SCRIPT_DIR%result\
        echo.
    )
    echo ------------------------------------------------------------
    echo.
)

:: ── Step 2: Generate Plotly failure charts ──────────────────────────────────────
echo ============================================================
echo  Generating Plotly failure charts...
echo ============================================================
echo.

"%PY%" "%PLOT_SCRIPT%"
if errorlevel 1 (
    echo.
    echo [ERROR] Chart generation failed. See output above for details.
    echo         Make sure result\GuLog_FailedSummary.csv exists.
) else (
    echo.
    echo [DONE]  Charts opened in browser.
)

echo.
echo ============================================================
echo  ALL COMPLETE
echo ============================================================
echo.
pause
endlocal
