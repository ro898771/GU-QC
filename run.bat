@echo off
setlocal

:: ============================================================
:: run_gufile_process.bat
:: Runs gufile_process.py on one or more GUFILE folders.
:: Usage:
::   Double-click  → processes GUFILE  (default)
::   run_gufile_process.bat GUFILE-old → processes GUFILE-old
::   run_gufile_process.bat GUFILE GUFILE-old → processes both
:: ============================================================

:: Locate script relative to this bat file
set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%src\main.py"

:: If no arguments supplied, default to GUFILE
if "%~1"=="" (
    set "FOLDERS=GUFILE"
) else (
    set "FOLDERS=%*"
)

echo ============================================================
echo  GU File Processor
echo  Script : %SCRIPT%
echo  Folder(s): %FOLDERS%
echo ============================================================
echo.

:: Process each folder in sequence
for %%F in (%FOLDERS%) do (
    echo [START] Processing folder: %%F
    echo.
    py "%SCRIPT%" %%F
    if errorlevel 1 (
        echo.
        echo [ERROR] Processing failed for folder: %%F
        echo.
    ) else (
        echo.
        echo [DONE]  Output written to: %SCRIPT_DIR%%%F\result\
        echo.
    )
    echo ------------------------------------------------------------
    echo.
)

echo ============================================================
echo  ALL FOLDERS COMPLETE
echo ============================================================
echo.
pause
endlocal
