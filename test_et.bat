@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================================
echo   SANAL CADIR (sanalcadir.com) SCRAPER BAGLANTI VE VERI CEKME TESTI
echo ============================================================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" test_scraper.py
) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" test_scraper.py
) else (
    python test_scraper.py
)
echo.
pause
