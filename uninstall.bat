@echo off
title PCMonitor v3.1 - Disinstallazione
color 0C
echo Rimuovo task e chiudo PCMonitor...
schtasks /end /tn "PCMonitor" >nul 2>&1
schtasks /delete /tn "PCMonitor" /f >nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PCMonitor.bat" del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PCMonitor.bat" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'pc_monitor\.py' -or $_.CommandLine -match 'PCMonitor' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }" >nul 2>&1
echo Fatto. I file e config.json restano nella cartella.
pause
