@echo off
setlocal
echo ============================================================
echo Building MP3 to ProTracker MOD Converter (MP3toMOD.exe)
echo ============================================================

REM Install requirements if needed
python -m pip install -r requirements.txt

REM Build onefile standalone executable
pyinstaller --noconfirm --clean --onefile --name "MP3toMOD" mp3tomod.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo BUILD SUCCESS! Standalone executable created:
    echo dist\MP3toMOD.exe
    echo.
    echo Note: Place ffmpeg.exe next to MP3toMOD.exe or in system PATH.
    echo ============================================================
) else (
    echo.
    echo [ERROR] Build failed with error code %ERRORLEVEL%!
)

pause
