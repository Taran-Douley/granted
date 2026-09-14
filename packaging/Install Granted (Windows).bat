@echo off
rem Double-click to install Granted for this folder. Safe to run again.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0.granted\install.ps1"
echo.
pause
