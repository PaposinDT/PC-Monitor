@echo off
cd /d "%~dp0"
title PCMonitor DEBUG
python pc_monitor.py
echo.
echo Programma terminato con codice %errorlevel%.
if exist pc_monitor_BOOT_ERROR.log type pc_monitor_BOOT_ERROR.log
pause
