@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在把依赖安装到 deps 目录，请稍候...
where py >nul 2>nul
if %errorlevel%==0 (
    py -m pip install --upgrade --target "%~dp0deps" pillow pymupdf python-docx tkinterdnd2 imageio-ffmpeg pywin32
) else (
    python -m pip install --upgrade --target "%~dp0deps" pillow pymupdf python-docx tkinterdnd2 imageio-ffmpeg pywin32
)
echo.
echo 安装完成。双击“启动.bat”即可打开界面。
pause
