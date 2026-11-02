@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "deps\flask\" (
    echo 首次运行，正在安装服务端依赖...
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -m pip install --upgrade --target "%~dp0deps" -r requirements.txt
    ) else (
        python -m pip install --upgrade --target "%~dp0deps" -r requirements.txt
    )
)
set "PYTHONPATH=%~dp0deps;%PYTHONPATH%"
where py >nul 2>nul
if %errorlevel%==0 (
    py app.py
) else (
    python app.py
)
pause
