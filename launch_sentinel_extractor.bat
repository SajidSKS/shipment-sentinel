@echo off
title Shipment Sentinel v4.0 - Data Extractor Workstation
cd /d "%~dp0"
echo ============================================================
echo      SHIPMENT SENTINEL v4.0 - DATA EXTRACTION WORKSTATION
echo ============================================================
echo Starting CustomTkinter extraction workstation...

REM Check local AppData Python 3.14 first, else fallback to standard python
if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    "%LOCALAPPDATA%\Python\bin\python.exe" sentinel_extractor.py
) else (
    python sentinel_extractor.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Python not found or encountered an error.
    echo Please make sure Python is installed and added to PATH.
    pause
)
