@echo off
rem ScanSplit PDF 启动脚本（完全离线，不联网）
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo 未找到虚拟环境 .venv，请先按 README 安装依赖。
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m scansplit.main
