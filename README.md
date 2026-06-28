# PC-Monitor

PC-Monitor is a lightweight remote management application that transforms Telegram into a powerful control panel for your Windows PC, 
providing file management, monitoring, messaging and remote administration through a clean interactive interface.

---

## Features

### Notifications
- PC startup notification
- Interactive Telegram menu
- Popup messages on the desktop
- Two-way chat between Telegram and the PC

### File Manager
- Browse drives (C:, D:, ...)
- Interactive folder navigation
- Download files from the PC
- Upload files from Telegram
- Automatic split for large files
- Automatic reconstruction of received file parts

### Desktop
- Screenshot capture
- Screen recording
- Live desktop preview
- Clipboard read/write

### Audio
- Record audio from the selected input device
- Select microphone directly from Telegram

### System
- System information
- CPU / RAM usage
- Disk usage
- IP address
- Running processes
- Temperature monitoring (when supported)

### Remote Actions
- Lock workstation
- Restart
- Shutdown
- Sleep
- Log off

### Communication
- Desktop popup messages
- Interactive replies
- Real-time chat

### Security
- Only accepts commands from the configured Telegram Chat ID
- Configuration stored locally
- No third-party services besides Telegram

---

## Requirements

- Windows 10 / 11
- Python 3.10+
- Telegram Bot
- Internet connection

---

## Installation

1. Download the latest release.

2. Extract the archive.

3. Run:

```
installa.bat
```

4. Enter:

- Bot Token
- Chat ID

5. Done.

PC-Monitor will automatically configure itself and create the startup task.

---

## Main Commands

| Command | Description |
|----------|-------------|
| Status | System information |
| Screenshot | Capture desktop |
| Screen Recording | Record screen |
| Live Screen | Live desktop preview |
| Browse Files | Interactive file browser |
| Upload File | Telegram → PC |
| Download File | PC → Telegram |
| Popup | Display desktop popup |
| Chat | Real-time chat |
| Processes | Running processes |
| Lock | Lock workstation |
| Restart | Restart computer |
| Shutdown | Shutdown computer |

---

## File Browser

The integrated browser allows you to:

- Navigate folders
- Open drives
- Download files
- Upload files
- Display current path
- Browse using interactive Telegram buttons

---

## Large Files

Files larger than the Telegram upload limit are automatically split into multiple parts.

Received parts are automatically reconstructed into the original file.

---

## Configuration

The application stores its configuration inside:

```
config.json
```

It contains:

- Telegram Bot Token
- Allowed Chat ID
- Preferences

---

## Startup

During installation PC-Monitor automatically creates a Windows Task Scheduler entry.

The application starts automatically every time you log in.

---

## Logs

Logs are stored in:

```
logs/
```

---

## License

This project is intended for personal use on systems you own or are authorized to administer.
