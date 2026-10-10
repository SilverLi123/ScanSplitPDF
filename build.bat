@echo off
rem ScanSplit PDF Windows 打包脚本（离线构建，产物 dist\ScanSplitPDF\）
rem 说明：本脚本只在本机（Windows 11 + Python 3.14）验证过，
rem 其他环境请对照 README 排查。
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 未找到虚拟环境 .venv，请先按 README 安装依赖。
    pause
    exit /b 1
)

echo [1/4] 检查依赖...
.venv\Scripts\python.exe -c "import rapidocr_onnxruntime, pypdfium2, onnxruntime, cv2, pypdf, PIL, numpy, yaml" || (
    echo 依赖缺失，执行：.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
    pause
    exit /b 1
)

echo [2/4] 安装打包工具（PyInstaller，仅构建时需要）...
.venv\Scripts\python.exe -m pip install pyinstaller==6.22.3 || (
    echo PyInstaller 安装失败
    pause
    exit /b 1
)

echo [3/4] 清理旧产物...
if exist build rmdir /s /q build
if exist dist\ScanSplitPDF rmdir /s /q dist\ScanSplitPDF

echo [4/4] PyInstaller 打包（含离线 OCR 模型）...
.venv\Scripts\python.exe -m PyInstaller scansplit.spec --noconfirm || (
    echo 打包失败
    pause
    exit /b 1
)

echo.
echo 构建完成：dist\ScanSplitPDF\ScanSplitPDF.exe
echo 自检：dist\ScanSplitPDF\ScanSplitPDF.exe --selftest
pause
