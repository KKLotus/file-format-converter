@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw batch_convert.py
    exit /b
)
where py >nul 2>nul
if %errorlevel%==0 (
    py batch_convert.py %*
    exit /b
)
python batch_convert.py %*
if errorlevel 1 pause
