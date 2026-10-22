@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================================
echo   SANAL CADIR (sanalcadir.com) KAPSAMLI TEST VE SISTEM KONTROLU
echo ============================================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" test_suite.py
) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" test_suite.py
) else (
    python test_suite.py
)
echo.
pause
