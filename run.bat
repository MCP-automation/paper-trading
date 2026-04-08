@echo off
setlocal enabledelayedexpansion

echo ============================================
echo    Binance Paper Trading System
echo    24/7 Automated Trading Dashboard
echo ============================================
echo.

cd /d "%~dp0"

set PYTHON=%~dp0venv\Scripts\python.exe

:: Check venv
if not exist "%PYTHON%" (
    echo ERROR: Virtual environment not found.
    echo Expected: %PYTHON%
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('"%PYTHON%" --version 2^>^&1') do set PYVER=%%i
echo [OK] %PYVER%

:: Check deps
"%PYTHON%" -c "import fastapi, uvicorn, pandas, numpy, sqlalchemy, binance, aiohttp" >nul 2>&1
if !errorlevel! neq 0 (
    echo Installing dependencies...
    "%PYTHON%" -m pip install --only-binary :all: -r backend\requirements.txt --quiet
)

echo.
echo Starting server in new window...
echo.

:: Launch server in a NEW visible Windows console window
start "Paper Trading Server" "%PYTHON%" backend\run_with_logging.py

echo.
echo ============================================
echo    Server is starting in a new window.
echo.
echo    Wait 10 seconds, then open:
echo    http://127.0.0.1:8000
echo ============================================
echo.
echo Log file: server_output.log
echo.
pause
