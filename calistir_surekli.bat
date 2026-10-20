@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================================
echo   SANAL CADIR BOTU - 7/24 SAATLIK DONGU MODU (--loop)
echo   (Kapatmak icin Ctrl+C tuslarina basabilirsiniz)
echo ============================================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --loop
) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" main.py --loop
) else (
    python main.py --loop
)
echo.
pause
