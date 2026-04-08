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
echo Checking dependencies...
"%PYTHON%" -c "import fastapi, uvicorn, pandas, numpy, sqlalchemy, binance, aiohttp" >nul 2>&1
if !errorlevel! neq 0 (
    echo Installing dependencies...
    "%PYTHON%" -m pip install --only-binary :all: -r backend\requirements.txt --quiet
    if !errorlevel! neq 0 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) else (
    echo [OK] All dependencies found
)

echo.
echo ============================================
echo    Starting server...
echo ============================================
echo.
echo The server will start in a new window.
echo Wait ~10 seconds, then open:
echo   http://127.0.0.1:8000
echo.
echo Log file: server_output.log
echo.
echo Press any key to start...
pause >nul

:: Launch server in a NEW visible Windows console
start "Paper Trading Server" "%PYTHON%" backend\run_with_logging.py

echo.
echo ============================================
echo    Server launched successfully!
echo ============================================
echo.
echo You can now access the dashboard at:
echo   http://127.0.0.1:8000
echo.
echo Check the server window for live logs.
echo.
pause
