@echo off
setlocal
cd /d "%~dp0"

echo [1/3] check pyinstaller
py -m PyInstaller --version >nul 2>&1
if errorlevel 1 py -m pip install --upgrade pyinstaller

echo [2/3] build (onedir, no temp extraction)
if not exist "tools\ncmdump.exe" goto no_tool

py -m PyInstaller --noconfirm --clean --onedir --windowed --name "NCM Studio" --add-binary "tools\ncmdump.exe;tools" gui_test_v3.py
if errorlevel 1 goto failed

echo [3/3] done: dist\NCM Studio\NCM Studio.exe
goto end

:no_tool
echo tools\ncmdump.exe not found, put it back first
goto end

:failed
echo build failed, copy the messages above

:end
pause
