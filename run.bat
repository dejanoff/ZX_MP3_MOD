@echo off
REM Launcher script for MP3 to ProTracker MOD converter
python "%~dp0mp3tomod.py" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Press any key to exit...
    pause >nul
)
