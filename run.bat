@echo off
setlocal

:: ============================================================
:: run.bat
::
:: Option 1 — Full run: extract ZIP files, build CONCAT CSVs,
::             then generate Plotly HTML charts.
::
:: Option 2 — Plot only: skip extraction, use the existing
::             result\ CONCAT files to regenerate charts.
::             (Use this after manually editing a CONCAT file.)
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%src\main.py"
set "PLOT_SCRIPT=%SCRIPT_DIR%src\plot.py"
set "RESULT_DIR=%SCRIPT_DIR%result"
set "ENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

:: Prefer the project venv python; fall back to system py
if exist "%ENV_PYTHON%" (
    set "PY=%ENV_PYTHON%"
) else (
    set "PY=py"
)

echo ============================================================
echo  GU-QC File Processor
echo  Python : %PY%
echo ============================================================
echo.
echo Select mode:
echo   1) Process ZIP files  ^(extract + concat, then generate plots^)
echo   2) Use existing result files  ^(skip extraction, regenerate plots only^)
echo.
set /p "MODE=Enter choice [1/2]: "
echo.

if "%MODE%"=="1" goto option1
if "%MODE%"=="2" goto option2

echo [ERROR] Invalid choice "%MODE%". Please enter 1 or 2.
echo.
pause
endlocal
goto :eof

:: ─────────────────────────────────────────────────────────────
:option1
echo [Option 1] Extracting ZIP files and building CONCAT CSVs...
echo ============================================================
echo.
"%PY%" "%SCRIPT%"
if errorlevel 1 (
    echo.
    echo [ERROR] Extraction failed. See output above.
    pause
    endlocal
    goto :eof
)
echo.
echo [DONE]  CONCAT files written to: %RESULT_DIR%\
echo.
goto generate_plots

:: ─────────────────────────────────────────────────────────────
:option2
if not exist "%RESULT_DIR%\" (
    echo [ERROR] No result\ folder found.
    echo         Run Option 1 first to generate the CONCAT files.
    echo.
    pause
    endlocal
    goto :eof
)
echo [Option 2] Using existing CONCAT files in: %RESULT_DIR%\
echo.
goto generate_plots

:: ─────────────────────────────────────────────────────────────
:generate_plots
echo ============================================================
echo  Generating HTML charts...
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
