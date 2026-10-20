@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================================
echo   SANAL CADIR BOTU - TEK SEFERLIK TARAMA (--once)
echo ============================================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --once
) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" main.py --once
) else (
    python main.py --once
)
echo.
pause
