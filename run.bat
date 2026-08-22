@echo off
cd /d "%~dp0"

:: Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WindowStyle Hidden"
    exit /b
)

:: Use pythonw so the administrator console does not remain visible.
if not exist ".\venv\Scripts\pythonw.exe" (
    powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Virtual environment is missing. Please reinstall dependencies.', 'Roblox AE Automation')"
    exit /b
)

start "" ".\venv\Scripts\pythonw.exe" "gui_app.py"
exit /b 0
