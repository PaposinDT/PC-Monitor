@echo off
setlocal EnableDelayedExpansion
title PCMonitor v4.1 - Installer
color 0A
cd /d "%~dp0"

echo.
echo  PCMonitor v4.1 - Installazione

echo [1/6] Verifico Python...
python --version
if errorlevel 1 (
  echo [ERRORE] Python non trovato. Installa Python e spunta Add Python to PATH.
  pause
  exit /b 1
)

echo [2/6] Creo cartelle...
for %%d in (logs ReceivedFiles SentFiles screenshots recordings) do if not exist "%%d" mkdir "%%d"

echo [3/6] Installo librerie necessarie...
python -m pip install --upgrade pip
python -m pip install requests requests-toolbelt psutil pyautogui pyperclip Pillow sounddevice numpy opencv-python mss
if errorlevel 1 (
  echo [ERRORE] Installazione librerie fallita.
  pause
  exit /b 1
)

echo [4/6] Creo config se manca...
if not exist "config.json" (
  if exist "config.example.json" (
    copy "config.example.json" "config.json" >nul
  ) else (
    echo {> config.json
    echo   "telegram": {"bot_token": "INSERISCI_QUI_IL_TOKEN", "authorized_chat_id": 123456789},>> config.json
    echo   "admin_password": "CambiamiSubito123!",>> config.json
    echo   "shell": {"enabled": true, "require_password": true}>> config.json
    echo }>> config.json
  )
  echo [ATTENZIONE] config.json creato. Aprilo, inserisci token e chat ID, poi rilancia questo file.
  notepad config.json
  pause
  exit /b 1
)

echo [5/6] Verifico sintassi del programma...
python -m py_compile pc_monitor.py
if errorlevel 1 (
  echo [ERRORE] pc_monitor.py contiene un errore di sintassi.
  pause
  exit /b 1
)

echo [6/6] Creo/aggiorno Task Scheduler...
for /f "tokens=*" %%i in ('where pythonw.exe 2^>nul') do if not defined PYTHONW set PYTHONW=%%i
if not defined PYTHONW set PYTHONW=pythonw.exe

schtasks /end /tn "PCMonitor" >nul 2>&1
schtasks /delete /tn "PCMonitor" /f >nul 2>&1
schtasks /end /tn "PC-Monitor" >nul 2>&1
schtasks /delete /tn "PC-Monitor" /f >nul 2>&1

schtasks /create /tn "PC-Monitor" /tr "\"%PYTHONW%\" \"%~dp0pc_monitor.py\"" /sc ONLOGON /ru "%USERNAME%" /delay 0000:20 /f
if errorlevel 1 (
  echo [ERRORE] Creazione task fallita.
  pause
  exit /b 1
)

echo.
echo Installazione completata.
echo Task creato: PC-Monitor
echo Percorso: "%~dp0pc_monitor.py"
echo.
echo Vuoi avviare PCMonitor ora? [S/N]
choice /c SN /n /m "Scelta: "
if errorlevel 2 goto end
start "" "%PYTHONW%" "%~dp0pc_monitor.py"
echo Avviato.

:end
pause
