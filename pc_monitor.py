"""
PCMonitor v4.1 STABLE - Controllo remoto PC via Telegram.
Funzioni: menu gerarchico, popup/chat, screenshot, MP4 screen recording, live screenshot, audio diagnostico, file browser stile Explorer, sistema, shell opzionale.
"""
from __future__ import annotations
import os, sys, json, time, html, wave, shutil, socket, logging, ctypes, datetime, threading, subprocess, webbrowser, traceback
from pathlib import Path
from queue import Queue, Empty

BASE_DIR = Path(__file__).parent.resolve()
BOOT_LOG = BASE_DIR / 'pc_monitor_BOOT_ERROR.log'

def boot_fail(where, exc):
    txt = f'BOOT ERROR in {where}: {exc}\n\n{traceback.format_exc()}\n'
    try:
        BOOT_LOG.write_text(txt, encoding='utf-8')
    except Exception:
        pass
    print(txt)
    try:
        input('Premi Invio per chiudere...')
    except Exception:
        pass
    sys.exit(1)

try:
    import requests
    import psutil
    import pyautogui
    import pyperclip
    import tkinter as tk
    from tkinter import scrolledtext
except Exception as e:
    boot_fail('import librerie base', e)

try:
    from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
except Exception:
    MultipartEncoder = None
    MultipartEncoderMonitor = None

try:
    import sounddevice as sd
    import numpy as np
except Exception:
    sd = None; np = None
try:
    import cv2, mss
except Exception:
    cv2 = None; mss = None

CONFIG_PATH = BASE_DIR / 'config.json'
DEFAULT_CONFIG = {
  'telegram': {'bot_token': 'INSERISCI_QUI_IL_TOKEN', 'authorized_chat_id': 123456789},
  'admin_password': 'CambiamiSubito123!',
  'shell': {'enabled': True, 'require_password': True},
  'folders': {'received_files':'ReceivedFiles','sent_files':'SentFiles','screenshots':'screenshots','recordings':'recordings','logs':'logs'},
  'popup': {'width':520,'height':310,'position':'top_right_soft','always_on_top':True,'reopen_delay_seconds':0},
  'monitoring': {'usb_notifications':True,'network_change_notifications':True,'login_notifications':True},
  'audio': {'default_record_seconds':10,'input_device':None,'samplerate':44100,'channels':1},
  'screen': {'default_record_seconds':5,'fps':8,'scale':0.7,'live_interval_seconds':2,'live_max_minutes':10}
}

def ensure_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding='utf-8')
        boot_fail('config', RuntimeError('config.json creato. Aprilo e inserisci token/chat ID.'))
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        boot_fail('lettura config.json', e)
    changed = False
    # Migrazione automatica da vecchio formato flat: bot_token / allowed_chat_id
    if 'telegram' not in cfg:
        old_token = cfg.get('bot_token') or cfg.get('BOT_TOKEN') or cfg.get('token')
        old_chat = cfg.get('allowed_chat_id') or cfg.get('ALLOWED_CHAT_ID') or cfg.get('chat_id')
        if old_token or old_chat:
            cfg['telegram'] = {
                'bot_token': old_token or DEFAULT_CONFIG['telegram']['bot_token'],
                'authorized_chat_id': old_chat or DEFAULT_CONFIG['telegram']['authorized_chat_id']
            }
            changed = True
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v; changed = True
        elif isinstance(v, dict):
            if not isinstance(cfg[k], dict):
                cfg[k] = v; changed = True
            else:
                for kk, vv in v.items():
                    if kk not in cfg[k]: cfg[k][kk] = vv; changed = True
    token = str(cfg.get('telegram',{}).get('bot_token',''))
    chat = str(cfg.get('telegram',{}).get('authorized_chat_id',''))
    if not token or token == 'INSERISCI_QUI_IL_TOKEN' or chat in ('', '0', '123456789'):
        if changed:
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
        boot_fail('config', RuntimeError('Token o Chat ID mancanti/non validi in config.json'))
    if changed: CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    return cfg
CFG = ensure_config()
TOKEN = CFG['telegram']['bot_token']; BOT_TOKEN = TOKEN; AUTHORIZED_ID = int(CFG['telegram']['authorized_chat_id'])
ADMIN_PASS = str(CFG.get('admin_password',''))
SHELL_ENABLED = bool(CFG.get('shell',{}).get('enabled',False)); SHELL_PASS = bool(CFG.get('shell',{}).get('require_password',True))
API_URL = f'https://api.telegram.org/bot{TOKEN}'
RECEIVED_DIR = BASE_DIR / CFG['folders']['received_files']; SENT_DIR = BASE_DIR / CFG['folders']['sent_files']; SCREENSHOTS_DIR = BASE_DIR / CFG['folders']['screenshots']; RECORDINGS_DIR = BASE_DIR / CFG['folders'].get('recordings','recordings'); LOGS_DIR = BASE_DIR / CFG['folders']['logs']
for d in [RECEIVED_DIR,SENT_DIR,SCREENSHOTS_DIR,RECORDINGS_DIR,LOGS_DIR]: d.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / f'pcmonitor_{datetime.date.today()}.log'
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler(sys.stdout)])
log = logging.getLogger('PCMonitor')

# Evita doppie istanze: se parte due volte, la seconda esce subito.
def ensure_single_instance():
    if os.name == 'nt':
        try:
            mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Global\\PCMonitor_v4_1_single_instance')
            if ctypes.windll.kernel32.GetLastError() == 183:
                log.warning('Istanza PCMonitor gia attiva. Esco per evitare messaggi doppi.')
                sys.exit(0)
            return mutex
        except Exception as e:
            log.warning(f'Controllo istanza singola fallito: {e}')
    return None

_APP_MUTEX = ensure_single_instance()

last_offset = 0; _popup_queue = Queue(); _tk_root = None; _popup_win = None; _chat_text_widget = None; _chat_active = False; _current_dir = Path.home(); _pending_action = None; _upload_target_dir = RECEIVED_DIR; _live_stop = threading.Event(); _network_last_ip = None; _path_registry = {}; _path_counter = 0

def esc(x): return html.escape(str(x))
def tg_get(method, params=None):
    try: return requests.get(f'{API_URL}/{method}', params=params, timeout=35).json()
    except Exception as e: log.error(f'tg_get {method}: {e}'); return {}
def tg_post(method, timeout=120, **kwargs):
    try:
        r = requests.post(f'{API_URL}/{method}', timeout=timeout, **kwargs)
        try: return r.json()
        except Exception: return {'ok': r.ok, 'text': r.text}
    except Exception as e: log.error(f'tg_post {method}: {e}'); return {}
def send_message(text, chat_id=None, parse_mode='HTML', reply_markup=None):
    chat_id = chat_id or AUTHORIZED_ID; last = None
    for chunk in ([text[i:i+3900] for i in range(0, len(text), 3900)] or ['']):
        payload = {'chat_id': chat_id, 'text': chunk, 'parse_mode': parse_mode}
        # Se non è specificata una inline keyboard, mantieni sempre il bottone Menu sotto la tastiera.
        payload['reply_markup'] = reply_markup if reply_markup is not None else menu_reply_markup()
        last = tg_post('sendMessage', json=payload)
    return last
def send_inline_keyboard(text, buttons, chat_id=None):
    kb = {'inline_keyboard': [[{'text': b[0], 'callback_data': b[1]} for b in row] for row in buttons]}
    return send_message(text, chat_id=chat_id, reply_markup=kb)
def answer_callback(cid, text=''): tg_post('answerCallbackQuery', json={'callback_query_id':cid,'text':text})

def menu_reply_markup():
    # Tastiera persistente sotto la chat Telegram. Rimane visibile anche quando i messaggi hanno inline keyboard.
    return {
        'keyboard': [[{'text':'🏠 Menu'}]],
        'resize_keyboard': True,
        'one_time_keyboard': False,
        'is_persistent': True
    }

def send_menu_keyboard_hint():
    return send_message('⌨️ Pulsante Menu rapido attivo sotto la tastiera.', reply_markup=menu_reply_markup())
def edit_message_text(message_id, text, reply_markup=None):
    payload={'chat_id':AUTHORIZED_ID,'message_id':message_id,'text':text,'parse_mode':'HTML'}
    if reply_markup is not None:
        payload['reply_markup']=reply_markup
    return tg_post('editMessageText', json=payload)

def _progress_bar(done, total, width=12):
    if not total:
        return '░' * width, 0
    pct = max(0, min(100, int(done * 100 / total)))
    filled = max(0, min(width, int(width * pct / 100)))
    return '█' * filled + '░' * (width - filled), pct

PUBLIC_BOT_UPLOAD_LIMIT = 49 * 1024 * 1024      # limite prudente API pubblica Telegram Bot (~50 MB)
SPLIT_PART_SIZE = 45 * 1024 * 1024              # parti sotto al limite, con margine

def send_document(path, caption=''):
    """Invia file PC -> Telegram.
    Nota: con l'API pubblica Telegram Bot i file grandi non possono essere inviati come singolo documento.
    Se il file supera ~50 MB viene diviso automaticamente in parti da 45 MB.
    """
    path = Path(path)
    if not path.exists() or not path.is_file():
        return send_message(f'❌ File non trovato: <code>{esc(path)}</code>')
    size = path.stat().st_size
    if size > PUBLIC_BOT_UPLOAD_LIMIT:
        return send_document_split(path, caption or path.name)
    return send_document_single(path, caption or path.name)

def send_document_split(path, caption=''):
    """Divide un file grande in parti Telegram-safe e invia ogni parte.
    Il destinatario potrà ricomporre le parti sul PC con copy /b.
    """
    path = Path(path)
    size = path.stat().st_size
    total_parts = (size + SPLIT_PART_SIZE - 1) // SPLIT_PART_SIZE
    if total_parts > 80:
        return send_message(
            f'❌ File troppo grande per invio a parti gestibile via bot.\n'
            f'<code>{esc(path.name)}</code>\n'
            f'Dimensione: {format_size(size)}\n'
            f'Parti previste: {total_parts}\n\n'
            f'Per file così grandi usa Local Bot API Server oppure trasferimento diretto/SSH.'
        )

    split_dir = SENT_DIR / f'parts_{safe_name(path.stem)}_{int(time.time())}'
    split_dir.mkdir(parents=True, exist_ok=True)
    send_message(
        f'📦 File grande rilevato: <code>{esc(path.name)}</code>\n'
        f'Dimensione: {format_size(size)}\n'
        f'Lo divido in {total_parts} parti da circa 45 MB e le invio una alla volta.'
    )

    created = []
    sent_ok = 0
    try:
        with path.open('rb') as src:
            for idx in range(1, total_parts + 1):
                part_path = split_dir / f'{path.name}.part{idx:03d}of{total_parts:03d}'
                with part_path.open('wb') as out:
                    remaining = SPLIT_PART_SIZE
                    while remaining > 0:
                        chunk = src.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        out.write(chunk)
                        remaining -= len(chunk)
                created.append(part_path)

        instructions = (
            f'📌 Ricomposizione su Windows dopo aver scaricato tutte le parti nella stessa cartella:\n'
            f'<code>copy /b "{esc(path.name)}.part*" "{esc(path.name)}"</code>'
        )
        send_message(instructions)

        for idx, part_path in enumerate(created, start=1):
            res = send_document_single(part_path, f'{caption} — parte {idx}/{total_parts}')
            if isinstance(res, dict) and res.get('ok'):
                sent_ok += 1
            else:
                send_message(f'❌ Invio interrotto alla parte {idx}/{total_parts}.')
                break
        if sent_ok == total_parts:
            send_message(f'✅ Tutte le parti inviate: {total_parts}/{total_parts}.')
        return {'ok': sent_ok == total_parts, 'parts_sent': sent_ok, 'parts_total': total_parts}
    except Exception as e:
        log.exception('send_document_split error')
        return send_message(f'❌ Errore split/invio file grande:\n<code>{esc(e)}</code>')
    finally:
        # Manteniamo le parti temporanee per debug/recupero. Pulizia automatica dei vecchi split.
        cleanup_old_parts()

def safe_name(name):
    return ''.join(c if c.isalnum() or c in ('-', '_', '.') else '_' for c in str(name))[:80] or 'file'

def cleanup_old_parts(max_age_hours=24):
    try:
        cutoff = time.time() - max_age_hours * 3600
        for p in SENT_DIR.glob('parts_*'):
            try:
                if p.is_dir() and p.stat().st_mtime < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

def send_document_single(path, caption=''):
    """Invia un singolo file sotto il limite dell'API pubblica.
    Modifica mirata: niente percentuale finta. Invia messaggi di stato affidabili:
    preparazione, upload iniziato, ancora in corso dopo 10s, poi ogni 20s, completato/fallito.
    """
    path = Path(path)
    if not path.exists() or not path.is_file():
        return send_message(f'❌ File non trovato: <code>{esc(path)}</code>')

    size = path.stat().st_size
    url = f'https://api.telegram.org/bot{TOKEN}/sendDocument'

    send_message(
        f'⬆️ Preparazione upload...\n'
        f'📄 <code>{esc(path.name)}</code>\n'
        f'{format_size(size)}'
    )

    done_event = threading.Event()
    result_box = {'res': None}

    def upload_worker():
        try:
            with open(str(path), 'rb') as f:
                files = {'document': (path.name, f, 'application/octet-stream')}
                data = {'chat_id': str(AUTHORIZED_ID), 'caption': caption or path.name}
                r = requests.post(url, data=data, files=files, timeout=(30, 3600))
            try:
                result_box['res'] = r.json()
            except Exception:
                result_box['res'] = {'ok': False, 'description': r.text[:1000]}
        except requests.exceptions.Timeout:
            result_box['res'] = {'ok': False, 'description': 'Timeout durante upload. Connessione lenta o file troppo grande.'}
        except Exception as e:
            result_box['res'] = {'ok': False, 'description': str(e)}
        finally:
            done_event.set()

    t = threading.Thread(target=upload_worker, daemon=True)
    t.start()

    send_message(
        f'🚀 Upload iniziato...\n'
        f'📄 <code>{esc(path.name)}</code>\n'
        f'{format_size(size)}'
    )

    start_time = time.time()
    first_notice_sent = False
    next_notice_after = 10.0

    while not done_event.wait(1.0):
        elapsed = int(time.time() - start_time)
        if elapsed >= next_notice_after:
            if not first_notice_sent:
                send_message(
                    f'⏳ Upload ancora in corso...\n'
                    f'📄 <code>{esc(path.name)}</code>\n'
                    f'{format_size(size)}\n\n'
                    f'Tempo trascorso: {elapsed} s'
                )
                first_notice_sent = True
                next_notice_after = elapsed + 20
            else:
                send_message(
                    f'⏳ Upload ancora in corso...\n'
                    f'📄 <code>{esc(path.name)}</code>\n'
                    f'Tempo trascorso: {elapsed} s'
                )
                next_notice_after = elapsed + 20

    t.join(timeout=1)
    res = result_box.get('res') or {'ok': False, 'description': 'Risposta upload mancante'}
    if res.get('ok'):
        send_message(
            f'✅ File inviato con successo.\n'
            f'📄 <code>{esc(path.name)}</code>\n'
            f'{format_size(size)}'
        )
    else:
        desc = esc(res.get('description') or 'Errore sconosciuto')
        send_message(
            f'❌ Upload fallito\n\n'
            f'📄 <code>{esc(path.name)}</code>\n'
            f'{format_size(size)}\n\n'
            f'Motivo:\n<code>{desc}</code>'
        )
    return res

def send_photo(path, caption=''):
    with open(str(path),'rb') as f: return tg_post('sendPhoto', data={'chat_id':AUTHORIZED_ID,'caption':caption}, files={'photo':f})
def send_audio_file(path, caption=''):
    with open(str(path),'rb') as f: return tg_post('sendAudio', data={'chat_id':AUTHORIZED_ID,'caption':caption}, files={'audio':f})
def download_file(file_id, dest_path):
    # Telegram Bot API standard: getFile permette download fino a circa 20 MB.
    # Scarichiamo in streaming con aggiornamenti di progresso su Telegram.
    info = tg_get('getFile', {'file_id': file_id})
    if not info.get('ok'):
        desc = info.get('description') or info.get('text') or 'getFile fallito'
        raise RuntimeError(f'Telegram getFile error: {desc}')
    result = info.get('result', {})
    fp = result.get('file_path')
    size = int(result.get('file_size') or 0)
    if not fp:
        raise RuntimeError('Telegram non ha restituito file_path. Il file potrebbe essere troppo grande per getFile (limite ~20 MB).')

    # Limite pratico del Bot API standard per download file.
    if size and size > 20 * 1024 * 1024:
        mb = size / (1024 * 1024)
        raise RuntimeError(
            f'File troppo grande per download via Bot API standard: {mb:.1f} MB (limite ~20 MB).\n'
            f'Soluzione: usa Local Bot API Server, oppure invia il file tramite link (Drive/Dropbox/WeTransfer).'
        )

    dest_path = Path(dest_path)
    file_name = dest_path.name
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.with_suffix(dest_path.suffix + '.part')

    # Messaggio di progresso iniziale
    size_label = format_size(size) if size else '?'
    status = send_message(
        f'⬇️ Download in corso...\n'
        f'<code>{esc(file_name)}</code>\n'
        f'Dimensione: {size_label}\n'
        f'░░░░░░░░░░░░ 0%\n\n'
        f'Connessione a Telegram...'
    )
    mid = (status.get('result') or {}).get('message_id') if isinstance(status, dict) else None
    last_update = {'t': 0.0, 'pct': -1, 'text': ''}

    def update_dl_progress(done, total, force=False, note='Download in corso...'):
        now = time.time()
        bar, pct = _progress_bar(done, total) if total else ('░░░░░░░░░░░░', 0)
        if not force and pct < 100:
            if (now - last_update['t']) < 2.0 and (pct - last_update['pct']) < 2:
                return
        last_update['t'] = now
        last_update['pct'] = pct
        done_label = format_size(done)
        total_label = format_size(total) if total else '?'
        text = (
            f'⬇️ Download in corso...\n'
            f'<code>{esc(file_name)}</code>\n'
            f'{done_label} / {total_label}\n'
            f'{bar} {pct}%\n\n'
            f'{note}'
        )
        if text == last_update.get('text'):
            return
        last_update['text'] = text
        if mid:
            edit_message_text(mid, text)

    url = f'https://api.telegram.org/file/bot{TOKEN}/{fp}'
    downloaded = 0
    try:
        with requests.get(url, stream=True, timeout=(20, 600)) as r:
            # Controlla HTTP status e segnala subito errori di rete
            if not r.ok:
                err_msg = f'HTTP {r.status_code}: {r.reason}'
                if mid:
                    edit_message_text(mid,
                        f'❌ Download fallito\n<code>{esc(file_name)}</code>\n\n'
                        f'Errore connessione Telegram:\n<code>{esc(err_msg)}</code>'
                    )
                raise RuntimeError(f'Errore HTTP dal server Telegram: {err_msg}')

            # Prendi la dimensione dall'header se Telegram non l'ha fornita in getFile
            if not size:
                content_length = r.headers.get('Content-Length')
                if content_length:
                    size_from_header = int(content_length)
                else:
                    size_from_header = 0
            else:
                size_from_header = size

            update_dl_progress(0, size_from_header, force=True, note='Connesso. Download avviato...')

            spinner = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
            spin_i = 0
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    spin_char = spinner[spin_i % len(spinner)]
                    spin_i += 1
                    update_dl_progress(
                        downloaded, size_from_header,
                        note=f'{spin_char} Scaricamento...'
                    )

        # Verifica integrità: confronta byte scaricati con dimensione attesa
        if size and downloaded < size:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            err = f'Download incompleto: ricevuti {format_size(downloaded)} su {format_size(size)} attesi.'
            if mid:
                edit_message_text(mid,
                    f'❌ Download interrotto\n<code>{esc(file_name)}</code>\n\n'
                    f'<code>{esc(err)}</code>\n\n'
                    f'Possibili cause: connessione instabile, timeout, file corrotto sul server.'
                )
            raise RuntimeError(err)

    except requests.exceptions.Timeout:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        err = 'Timeout durante il download. Connessione lenta o file troppo grande.'
        if mid:
            edit_message_text(mid,
                f'❌ Download fallito — Timeout\n<code>{esc(file_name)}</code>\n\n'
                f'<code>{esc(err)}</code>'
            )
        raise RuntimeError(err)
    except requests.exceptions.ConnectionError as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        err = f'Errore di connessione: {e}'
        if mid:
            edit_message_text(mid,
                f'❌ Download fallito — Connessione\n<code>{esc(file_name)}</code>\n\n'
                f'<code>{esc(str(e)[:300])}</code>\n\nControlla la connessione internet del PC.'
            )
        raise RuntimeError(err)
    except Exception as e:
        # Se non è già un errore gestito sopra, segnalalo e rimuovi il file parziale
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        if mid:
            edit_message_text(mid,
                f'❌ Download fallito\n<code>{esc(file_name)}</code>\n\n'
                f'<code>{esc(str(e)[:400])}</code>'
            )
        raise

    # Rinomina se il file esiste già
    if dest_path.exists():
        stem, suffix = dest_path.stem, dest_path.suffix
        i = 1
        while True:
            candidate = dest_path.with_name(f'{stem}_{i}{suffix}')
            if not candidate.exists():
                dest_path = candidate
                break
            i += 1
    tmp.replace(dest_path)

    # Aggiorna messaggio finale con conferma e percorso
    if mid:
        edit_message_text(mid,
            f'✅ File ricevuto e salvato:\n<code>{esc(dest_path)}</code>\n'
            f'Dimensione: {format_size(downloaded)}'
        )
    return dest_path

# menu

def available_drive_buttons():
    buttons=[]
    if os.name == 'nt':
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            root = Path(f'{letter}:/')
            try:
                if root.exists():
                    buttons.append((f'💽 {letter}:', f'b:{register_path(root)}:0'))
            except Exception:
                pass
    else:
        buttons.append(('💽 /', f'b:{register_path(Path("/"))}:0'))
    return buttons

def send_main_menu(text='🤖 <b>PCMonitor v4.1</b> — scegli categoria:'):
    send_inline_keyboard(text, [[('🖥️ Schermo','menu:screen'),('🎙️ Audio','menu:audio')],[('💬 Messaggi','menu:comm'),('📁 File','menu:files')],[('⚙️ Sistema','menu:system'),('📋 Clipboard','menu:clip')],[('🧰 Shell','menu:shell'),('❓ Help','cmd:help')]])
def send_screen_menu(): send_inline_keyboard('🖥️ <b>Schermo</b>', [[('📸 Screenshot','cmd:screenshot')],[('📹 MP4 5s','screenrec:5'),('📹 MP4 10s','screenrec:10')],[('📹 MP4 20s','screenrec:20')],[('🔴 Live start','live:start'),('⏹ Live stop','live:stop')],[('⬅️ Indietro','menu:main')]])
def send_audio_menu(): send_inline_keyboard('🎙️ <b>Audio</b>', [[('🎧 Lista microfoni','cmd:audiodevices')],[('🎙️ Registra 5s','record:5'),('🎙️ Registra 10s','record:10')],[('🎙️ Registra 20s','record:20')],[('⬅️ Indietro','menu:main')]])
def send_comm_menu(): send_inline_keyboard('💬 <b>Messaggi</b>', [[('📩 Popup','ask:popup'),('💬 Chat','ask:chat')],[('💬 Apri chat','cmd:chat_open')],[('⬅️ Indietro','menu:main')]])
def send_files_menu():
    rows = [[('📂 Browser interattivo','cmd:browse')]]
    drives = available_drive_buttons()
    if drives:
        rows.append(drives[:3])
        if len(drives) > 3:
            rows.append(drives[3:6])
    rows += [[('📤 Upload qui','cmd:upload_here'),('🔎 Cerca file','ask:search')],[('⬇️ Download path','ask:download')],[('⬅️ Indietro','menu:main')]]
    send_inline_keyboard('📁 <b>File</b>', rows)

def send_system_menu(): send_inline_keyboard('⚙️ <b>Sistema</b>', [[('📊 Status','cmd:status'),('📋 Processi','cmd:processes')],[('🔒 Lock','cmd:lock'),('😴 Sleep','cmd:sleep')],[('🔄 Reboot','ask:restart'),('⏻ Shutdown','ask:shutdown')],[('🚪 Logout','ask:logout'),('🌐 Apri URL','ask:openurl')],[('⬅️ Indietro','menu:main')]])
def send_clip_menu(): send_inline_keyboard('📋 <b>Clipboard</b>', [[('📋 Leggi','cmd:clipboard'),('✍️ Imposta','ask:setclipboard')],[('⬅️ Indietro','menu:main')]])
def send_shell_menu(): send_inline_keyboard('🧰 <b>Shell</b>', [[('PowerShell','ask:shell'),('CMD','ask:cmd')],[('⬅️ Indietro','menu:main')]])

# system
def get_local_ip():
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); ip=s.getsockname()[0]; s.close(); return ip
    except Exception: return 'N/D'
def get_public_ip():
    try: return requests.get('https://api.ipify.org', timeout=5).text.strip()
    except Exception: return 'N/D'
def get_uptime():
    up=time.time()-psutil.boot_time(); h,r=divmod(int(up),3600); m,s=divmod(r,60); return f'{h}h {m}m {s}s'
def get_temperatures_text():
    cpu_temp = 'N/D'
    gpu_temp = 'N/D'
    try:
        temps = getattr(psutil, 'sensors_temperatures', lambda: {})() or {}
        vals = []
        for entries in temps.values():
            for e in entries:
                if getattr(e, 'current', None) is not None:
                    vals.append(float(e.current))
        if vals:
            cpu_temp = f'{max(vals):.0f}°C'
    except Exception:
        pass
    if os.name == 'nt' and cpu_temp == 'N/D':
        try:
            cmd = 'Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace "root/wmi" | Select-Object -First 1 -ExpandProperty CurrentTemperature'
            r = subprocess.run(['powershell','-NoProfile','-Command',cmd], capture_output=True, text=True, timeout=5)
            raw = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ''
            if raw:
                c = (float(raw) / 10.0) - 273.15
                if -20 < c < 120:
                    cpu_temp = f'{c:.0f}°C'
        except Exception:
            pass
    try:
        r = subprocess.run(['nvidia-smi','--query-gpu=temperature.gpu','--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
        raw = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ''
        if raw:
            gpu_temp = f'{float(raw):.0f}°C'
    except Exception:
        pass
    return cpu_temp, gpu_temp
def get_status_text():
    cpu=psutil.cpu_percent(interval=0.5); mem=psutil.virtual_memory(); disk=psutil.disk_usage('C:\\'); cpu_t,gpu_t=get_temperatures_text()
    return f'🖥️ <b>Stato PC</b>\n👤 Utente: <code>{esc(os.environ.get("USERNAME","N/D"))}</code>\n💻 Host: <code>{esc(socket.gethostname())}</code>\n⏱ Uptime: <code>{get_uptime()}</code>\n🌐 IP locale: <code>{get_local_ip()}</code>\n🌍 IP pubblico: <code>{get_public_ip()}</code>\n🧠 CPU: <code>{cpu}%</code> | 🌡️ <code>{cpu_t}</code>\n🎮 GPU temp: <code>{gpu_t}</code>\n💾 RAM: <code>{mem.percent}% ({mem.used//1024**2}/{mem.total//1024**2} MB)</code>\n💿 Disco C: <code>{disk.percent}% ({disk.free//1024**3} GB liberi)</code>'
def take_screenshot():
    ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); p=SCREENSHOTS_DIR/f'screen_{ts}.png'; pyautogui.screenshot(str(p)); return p
def record_screen_mp4(seconds=5):
    if cv2 is None or mss is None or np is None: raise RuntimeError('Mancano opencv-python/mss/numpy. Esegui installa.bat.')
    seconds=max(1,min(int(seconds),60)); fps=int(CFG['screen'].get('fps',8)); scale=float(CFG['screen'].get('scale',0.7)); ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); out=RECORDINGS_DIR/f'screenrec_{ts}.mp4'
    with mss.mss() as sct:
        mon=sct.monitors[1]; w,h=int(mon['width']*scale), int(mon['height']*scale); writer=cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*'mp4v'), fps, (w,h)); end=time.time()+seconds; delay=1/fps
        while time.time()<end:
            img=sct.grab(mon); frame=np.array(img)[:,:,:3]; frame=cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            if scale != 1.0: frame=cv2.resize(frame,(w,h),interpolation=cv2.INTER_AREA)
            writer.write(frame); time.sleep(delay)
        writer.release()
    return out
def live_screen_loop(interval=2):
    _live_stop.clear(); send_message('🔴 Live screen avviato. Uso /live stop per fermare.'); end=time.time()+int(CFG['screen'].get('live_max_minutes',10))*60
    while not _live_stop.is_set() and time.time()<end:
        try: send_photo(take_screenshot(), '🔴 Live screen')
        except Exception as e: send_message(f'❌ Live error: {esc(e)}'); break
        time.sleep(max(1,int(interval)))
    send_message('⏹ Live screen fermato.')
def audio_devices_text():
    if sd is None: return '❌ sounddevice non importato.'
    try:
        devices=sd.query_devices(); hostapis=sd.query_hostapis(); lines=['🎧 <b>Dispositivi audio input</b>\n']
        current = CFG.get('audio',{}).get('input_device',None)
        for i,d in enumerate(devices):
            if d.get('max_input_channels',0)>0:
                host=hostapis[d['hostapi']]['name'] if 'hostapi' in d else '?'; mark=''
                if current is not None and str(current) == str(i): mark=' ✅ scelto'
                else:
                    try:
                        if current is None and i == sd.default.device[0]: mark=' ✅ default'
                    except Exception: pass
                lines.append(f'<code>{i}</code> — {esc(d["name"])} | ch:{d["max_input_channels"]} | {esc(host)}{mark}')
        lines.append('\nPuoi scegliere dal menu Audio con i bottoni, oppure nel config: audio.input_device = numero.')
        return '\n'.join(lines) if len(lines)>1 else '❌ Nessun microfono input trovato.'
    except Exception as e: return f'❌ Errore device audio: {esc(e)}'
def send_audio_devices_menu():
    if sd is None:
        send_message('❌ sounddevice non importato.')
        return
    try:
        devices = sd.query_devices(); rows=[]
        for i,d in enumerate(devices):
            if d.get('max_input_channels',0)>0:
                rows.append((f'{i} — {d["name"][:22]}', f'audioset:{i}'))
        buttons=[rows[i:i+1] for i in range(0, min(len(rows), 20), 1)]
        buttons.append([('Usa default','audioset:default')])
        buttons.append([('⬅️ Indietro','menu:audio')])
        send_inline_keyboard(audio_devices_text(), buttons)
    except Exception as e:
        send_message(f'❌ Errore device audio: {esc(e)}')
def set_audio_device(value):
    if value == 'default':
        CFG['audio']['input_device'] = None
    else:
        CFG['audio']['input_device'] = int(value)
    CONFIG_PATH.write_text(json.dumps(CFG, indent=2), encoding='utf-8')
    send_message(f'✅ Microfono impostato su: <code>{esc(CFG["audio"]["input_device"])}</code>\nRiavvia lo script se una registrazione era già in corso.')
def record_audio(seconds=10):
    if sd is None or np is None: raise RuntimeError('Mancano sounddevice/numpy. Esegui installa.bat.')
    seconds=max(1,min(int(seconds),120)); sr=int(CFG['audio'].get('samplerate',44100)); ch=int(CFG['audio'].get('channels',1)); dev=CFG['audio'].get('input_device',None)
    if dev in ('','null'): dev=None
    if dev is not None: dev=int(dev)
    ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); p=SENT_DIR/f'audio_{ts}.wav'; data=sd.rec(int(seconds*sr), samplerate=sr, channels=ch, dtype='int16', device=dev); sd.wait(); peak=int(np.max(np.abs(data))) if data.size else 0
    with wave.open(str(p),'wb') as wf: wf.setnchannels(ch); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(data.tobytes())
    return p, peak
def run_shell(cmd, shell_type='powershell'):
    try:
        if shell_type=='powershell': r=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',cmd], capture_output=True, text=True, timeout=45, encoding='utf-8', errors='replace')
        else: r=subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=45, encoding='utf-8', errors='replace')
        out=r.stdout.strip(); err=r.stderr.strip(); return (out + (f'\n[stderr]\n{err}' if err else '')).strip() or '(nessun output)'
    except subprocess.TimeoutExpired: return '⚠️ Timeout.'
    except Exception as e: return f'❌ Errore: {e}'
def list_processes():
    psutil.cpu_percent(interval=None); time.sleep(0.3); rows=[]
    for p in psutil.process_iter(['pid','name','cpu_percent','memory_info']):
        try: rows.append(p.info)
        except Exception: pass
    rows.sort(key=lambda x: x.get('cpu_percent') or 0, reverse=True); lines=['<b>Top 25 processi</b>\n']
    for p in rows[:25]:
        mem=(p['memory_info'].rss//1024//1024) if p.get('memory_info') else 0; lines.append(f'• <code>{esc(p["name"])}</code> PID:{p["pid"]} CPU:{p.get("cpu_percent",0)}% RAM:{mem}MB')
    return '\n'.join(lines)
def lock_screen(): ctypes.windll.user32.LockWorkStation()
def sleep_pc(): subprocess.run(['rundll32.exe','powrprof.dll,SetSuspendState','0,1,0'])
def logout_pc(): subprocess.run(['shutdown','/l'])
def shutdown_pc(): subprocess.run(['shutdown','/s','/t','5'])
def restart_pc(): subprocess.run(['shutdown','/r','/t','5'])
def open_url(url):
    if not url.startswith(('http://','https://')): url='https://'+url
    webbrowser.open(url)

# files
def register_path(path):
    global _path_counter
    _path_counter += 1
    key = str(_path_counter)
    _path_registry[key] = str(path)
    if len(_path_registry) > 500:
        for k in list(_path_registry.keys())[:100]:
            _path_registry.pop(k, None)
    return key
def path_from_key(key):
    return Path(_path_registry.get(str(key), str(key)))
def format_size(b):
    for u in ['B','KB','MB','GB','TB']:
        if b<1024: return f'{b:.1f}{u}'
        b/=1024
    return f'{b:.1f}PB'
def send_file_browser(path=None, page=0):
    global _current_dir
    if path:
        _current_dir = Path(path)
    target = _current_dir
    try:
        if not target.exists() or not target.is_dir():
            send_message(f'❌ Cartella non valida: <code>{esc(target)}</code>')
            return
        all_items = sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        send_message(f'❌ Accesso negato: <code>{esc(target)}</code>')
        return
    except Exception as e:
        send_message(f'❌ Errore: {esc(e)}')
        return

    per_page = 12
    total = len(all_items)
    max_page = max(0, (total - 1) // per_page) if total else 0
    try:
        page = int(page)
    except Exception:
        page = 0
    page = max(0, min(page, max_page))
    shown = all_items[page * per_page:(page + 1) * per_page]

    lines = [
        f'📂 <b>File browser</b>',
        f'📌 Cartella: <code>{esc(target)}</code>',
        f'📄 Elementi: <code>{total}</code> | Pagina <code>{page + 1}/{max_page + 1}</code>\n'
    ]
    buttons = []

    drive_buttons = available_drive_buttons()
    if drive_buttons:
        buttons.append(drive_buttons[:4])

    # Riga navigazione alta
    nav_top = []
    if target.parent != target:
        nav_top.append(('⬆️ Su', f'b:{register_path(target.parent)}:0'))
    nav_top.append(('🔄 Aggiorna', f'b:{register_path(target)}:{page}'))
    nav_top.append(('📌 Percorso', f'path:{register_path(target)}'))
    buttons.append(nav_top)

    if not shown:
        lines.append('Cartella vuota.')
    else:
        for item in shown:
            if item.is_dir():
                lines.append(f'📁 <code>{esc(item.name)}</code>')
                buttons.append([(f'📁 {item.name[:42]}', f'b:{register_path(item)}:0')])
            else:
                try:
                    sz = format_size(item.stat().st_size)
                except Exception:
                    sz = '?'
                lines.append(f'📄 <code>{esc(item.name)}</code> ({sz})')
                buttons.append([(f'⬇️ {item.name[:42]}', f'd:{register_path(item)}')])

    # Paginazione
    nav = []
    if page > 0:
        nav.append(('⬅️ Prec', f'b:{register_path(target)}:{page-1}'))
    if page < max_page:
        nav.append(('➡️ Succ', f'b:{register_path(target)}:{page+1}'))
    if nav:
        buttons.append(nav)

    buttons.append([('📤 Upload qui', f'u:{register_path(target)}'), ('📌 Mostra percorso', f'path:{register_path(target)}')])
    buttons.append([('🖥️ Menu', 'menu:main')])
    send_inline_keyboard('\n'.join(lines), buttons)

def search_files(name, root=None):
    root=Path(root) if root else Path.home(); res=[]
    try:
        for p in root.rglob(f'*{name}*'):
            res.append(str(p))
            if len(res)>=30: break
    except Exception as e: return f'⚠️ Ricerca parziale: {esc(e)}'
    return 'Nessun file trovato.' if not res else '🔎 <b>Risultati</b>\n'+'\n'.join(f'<code>{esc(r)}</code>' for r in res)

# UI
def _tk_main_thread():
    global _tk_root
    _tk_root=tk.Tk(); _tk_root.withdraw()
    def process_queue():
        try:
            while True:
                cmd,args=_popup_queue.get_nowait()
                if cmd=='popup': _show_popup(args['text'])
                elif cmd=='chat_open': _open_chat(args['text'])
                elif cmd=='chat_append': _append_chat(args['text'])
        except Empty: pass
        _tk_root.after(200, process_queue)
    _tk_root.after(200, process_queue); _tk_root.mainloop()
def _position_window(win,w,h):
    sw,sh=win.winfo_screenwidth(), win.winfo_screenheight(); pos=CFG.get('popup',{}).get('position','top_right_soft')
    if pos=='center': x,y=(sw-w)//2,(sh-h)//2
    else: x,y=sw-w-140,90
    return f'{w}x{h}+{x}+{y}'
def _show_popup(text):
    global _popup_win
    try:
        if _popup_win and _popup_win.winfo_exists(): _popup_win.destroy()
    except Exception: pass
    w=int(CFG['popup']['width']); h=int(CFG['popup']['height']); win=tk.Toplevel(_tk_root); _popup_win=win; win.title('📩 PCMonitor'); win.attributes('-topmost',bool(CFG['popup']['always_on_top'])); win.resizable(False,False); win.configure(bg='#1e1e2e'); win.geometry(_position_window(win,w,h)); win.protocol('WM_DELETE_WINDOW', lambda: win.destroy())
    tk.Label(win,text='💬 Nuovo messaggio',bg='#1e1e2e',fg='#cdd6f4',font=('Segoe UI',11,'bold')).pack(pady=(12,4))
    tk.Label(win,text=text,bg='#313244',fg='#cdd6f4',font=('Segoe UI',10),wraplength=w-30,justify='left',padx=8,pady=8).pack(fill='x',padx=10,pady=4)
    tk.Label(win,text='Risposta:',bg='#1e1e2e',fg='#a6adc8',font=('Segoe UI',9)).pack(anchor='w',padx=12)
    rv=tk.StringVar(); entry=tk.Entry(win,textvariable=rv,bg='#313244',fg='#cdd6f4',insertbackground='#cdd6f4',relief='flat',font=('Segoe UI',10)); entry.pack(fill='x',padx=12,pady=(2,8)); entry.focus(); fr=tk.Frame(win,bg='#1e1e2e'); fr.pack(fill='x',padx=12,pady=(0,12))
    def ok(): send_message('✅ Popup letto / Ho capito.'); win.destroy()
    def send():
        r=rv.get().strip()
        if r: threading.Thread(target=send_message,args=(f'💬 Risposta dal PC:\n{esc(r)}',),daemon=True).start()
        win.destroy()
    tk.Button(fr,text='✅ Ho capito',command=ok,bg='#a6e3a1',fg='#1e1e2e',font=('Segoe UI',10,'bold'),relief='flat',padx=12,pady=4).pack(side='left')
    tk.Button(fr,text='📤 Rispondi',command=send,bg='#89b4fa',fg='#1e1e2e',font=('Segoe UI',10,'bold'),relief='flat',padx=12,pady=4).pack(side='right'); entry.bind('<Return>',lambda e: send())
def _open_chat(initial_msg=''):
    global _chat_text_widget,_chat_active
    if _chat_active and _chat_text_widget: _append_chat(f'[Telegram]: {initial_msg}'); return
    _chat_active=True; win=tk.Toplevel(_tk_root); win.title('💬 PCMonitor Chat'); win.attributes('-topmost',True); win.geometry('520x420'); win.configure(bg='#1e1e2e')
    def close():
        global _chat_active,_chat_text_widget
        _chat_active=False; _chat_text_widget=None; win.destroy(); send_message('💬 Chat chiusa sul PC.')
    win.protocol('WM_DELETE_WINDOW',close); txt=scrolledtext.ScrolledText(win,bg='#181825',fg='#cdd6f4',font=('Consolas',9),state='disabled',relief='flat',wrap='word'); txt.pack(fill='both',expand=True,padx=8,pady=8); _chat_text_widget=txt
    if initial_msg: _append_chat(f'[Telegram]: {initial_msg}')
    bot=tk.Frame(win,bg='#1e1e2e'); bot.pack(fill='x',padx=8,pady=(0,8)); mv=tk.StringVar(); ent=tk.Entry(bot,textvariable=mv,bg='#313244',fg='#cdd6f4',insertbackground='#cdd6f4',relief='flat',font=('Segoe UI',10)); ent.pack(side='left',fill='x',expand=True,padx=(0,6)); ent.focus()
    def send():
        msg=mv.get().strip()
        if not msg: return
        _append_chat(f'[PC]: {msg}'); threading.Thread(target=send_message,args=(f'🖥️ PC dice:\n{esc(msg)}',),daemon=True).start(); mv.set('')
    ent.bind('<Return>',lambda e: send()); tk.Button(bot,text='Invia',command=send,bg='#89b4fa',fg='#1e1e2e',relief='flat',font=('Segoe UI',10,'bold'),padx=10).pack(side='right')
def _append_chat(text):
    if _chat_text_widget:
        _chat_text_widget.configure(state='normal'); _chat_text_widget.insert('end',text+'\n'); _chat_text_widget.see('end'); _chat_text_widget.configure(state='disabled')
def show_popup(text): _popup_queue.put(('popup',{'text':text}))
def open_chat(text): _popup_queue.put(('chat_open',{'text':text}))
def append_chat(text): _popup_queue.put(('chat_append',{'text':text}))

def ask(action,prompt):
    global _pending_action
    _pending_action=action; send_message(prompt)
def handle_pending(text):
    global _pending_action
    action=_pending_action; _pending_action=None
    if action=='popup': show_popup(text); send_message('✅ Popup inviato.')
    elif action=='chat': open_chat(text); send_message('💬 Chat aperta.')
    elif action=='search': send_message(search_files(text))
    elif action=='download':
        p=Path(text.strip()); send_document(p,p.name) if p.exists() and p.is_file() else send_message(f'❌ File non trovato: <code>{esc(p)}</code>')
    elif action=='setclipboard': pyperclip.copy(text); send_message('✅ Clipboard impostata.')
    elif action=='openurl': open_url(text); send_message(f'🌐 Aperto: <code>{esc(text)}</code>')
    elif action in ('shutdown','restart','logout'):
        if text.strip()!=ADMIN_PASS: send_message('❌ Password errata.'); return
        if action=='shutdown': send_message('⏻ Spegnimento tra 5s.'); shutdown_pc()
        elif action=='restart': send_message('🔄 Riavvio tra 5s.'); restart_pc()
        else: send_message('🚪 Logout.'); logout_pc()
    elif action in ('shell','cmd'):
        if not SHELL_ENABLED: send_message('❌ Shell disabilitata.'); return
        if not text.startswith(ADMIN_PASS+' '): send_message('❌ Formato: password spazio comando'); return
        command=text[len(ADMIN_PASS)+1:]; out=run_shell(command,'powershell' if action=='shell' else 'cmd'); send_message(f'<pre>{esc(out[:3800])}</pre>')

def handle_command(text,message=None):
    global _current_dir,_pending_action
    if _pending_action and not text.startswith('/'): handle_pending(text); return
    parts=text.strip().split(None,2); cmd=parts[0].lower()
    try:
        if cmd in ('/start','/menu'): send_main_menu()
        elif cmd=='/help': send_message('🤖 <b>Comandi principali</b>\n/menu\n/status\n/screenshot\n/screenrec [sec]\n/live start|stop\n/record [sec]\n/audiodevices\n/popup testo\n/chat testo\n/browse [path]\n/uploadhere\n/download path\n/search nome\n/clipboard\n/setclipboard testo\n/lock\n/restart password\n/shutdown password\n/logout password\n/sleep\n/shell password comando\n/cmd password comando')
        elif cmd=='/status': send_message(get_status_text())
        elif cmd=='/screenshot': send_photo(take_screenshot(),'📸 Screenshot')
        elif cmd=='/screenrec':
            secs=int(parts[1]) if len(parts)>1 and parts[1].isdigit() else int(CFG['screen']['default_record_seconds'])
            def sr():
                try: send_message(f'⏺ Registro MP4 {secs}s...'); send_document(record_screen_mp4(secs),f'📹 Screen recording MP4 ({secs}s)')
                except Exception as e: send_message(f'❌ Screenrec error: {esc(e)}')
            threading.Thread(target=sr,daemon=True).start()
        elif cmd=='/live':
            arg=parts[1].lower() if len(parts)>1 else 'start'
            if arg=='stop': _live_stop.set(); send_message('⏹ Stop richiesto.')
            else: threading.Thread(target=live_screen_loop,args=(int(CFG['screen'].get('live_interval_seconds',2)),),daemon=True).start()
        elif cmd=='/record':
            secs=int(parts[1]) if len(parts)>1 and parts[1].isdigit() else int(CFG['audio']['default_record_seconds'])
            def rec():
                try:
                    send_message(f'🎙️ Registro audio {secs}s...'); p,peak=record_audio(secs)
                    if peak<150: send_message('⚠️ Audio quasi muto. Usa /audiodevices e scegli un microfono dai bottoni, oppure imposta audio.input_device nel config.')
                    send_audio_file(p,f'🎙️ Audio {secs}s | peak {peak}')
                except Exception as e: send_message(f'❌ Audio error: {esc(e)}')
            threading.Thread(target=rec,daemon=True).start()
        elif cmd=='/audiodevices': send_audio_devices_menu()
        elif cmd=='/popup':
            testo=text[len('/popup'):].strip()
            if testo: show_popup(testo); send_message('✅ Popup mostrato.')
            else: ask('popup','✍️ Scrivi ora il testo da mostrare nel popup.')
        elif cmd=='/chat':
            testo=text[len('/chat'):].strip() or 'Chat aperta.'
            if _chat_active: append_chat(f'[Telegram]: {testo}')
            else: open_chat(testo)
            send_message('💬 Chat aggiornata/aperta.')
        elif cmd=='/browse': send_file_browser(text[len('/browse'):].strip() or None)
        elif cmd=='/uploadhere':
            global _upload_target_dir
            _upload_target_dir = _current_dir
            send_message(f'📤 Ora manda un file a questo bot. Lo salvo in:\n<code>{esc(_upload_target_dir)}</code>')
        elif cmd=='/pwd': send_message(f'📌 <code>{esc(_current_dir)}</code>')
        elif cmd=='/download':
            p=Path(text[len('/download'):].strip()); send_document(p,p.name) if p.exists() and p.is_file() else send_message(f'❌ File non trovato: <code>{esc(p)}</code>')
        elif cmd=='/search': send_message(search_files(text[len('/search'):].strip()))
        elif cmd=='/clipboard': send_message(f'📋 Clipboard:\n<code>{esc(pyperclip.paste() or "(vuoto)")}</code>')
        elif cmd=='/setclipboard': pyperclip.copy(text[len('/setclipboard'):].strip()); send_message('✅ Clipboard impostata.')
        elif cmd=='/processes': send_message(list_processes())
        elif cmd=='/openurl': open_url(text[len('/openurl'):].strip()); send_message('🌐 URL aperto.')
        elif cmd=='/lock': lock_screen(); send_message('🔒 Schermo bloccato.')
        elif cmd=='/sleep': send_message('😴 Sospensione.'); sleep_pc()
        elif cmd in ('/restart','/reboot'):
            pw=parts[1] if len(parts)>1 else ''
            if pw!=ADMIN_PASS: send_message('❌ Password errata.'); return
            send_message('🔄 Riavvio tra 5s.'); restart_pc()
        elif cmd=='/shutdown':
            pw=parts[1] if len(parts)>1 else ''
            if pw!=ADMIN_PASS: send_message('❌ Password errata.'); return
            send_message('⏻ Spegnimento tra 5s.'); shutdown_pc()
        elif cmd=='/logout':
            pw=parts[1] if len(parts)>1 else ''
            if pw!=ADMIN_PASS: send_message('❌ Password errata.'); return
            send_message('🚪 Logout.'); logout_pc()
        elif cmd in ('/shell','/cmd'):
            if not SHELL_ENABLED: send_message('❌ Shell disabilitata.'); return
            if SHELL_PASS:
                if len(parts)<3 or parts[1]!=ADMIN_PASS: send_message(f'Uso: {cmd} password comando'); return
                command=parts[2]
            else: command=text[len(cmd):].strip()
            out=run_shell(command,'powershell' if cmd=='/shell' else 'cmd'); send_message(f'<pre>{esc(out[:3800])}</pre>')
        else:
            if _chat_active: append_chat(f'[Telegram]: {text}')
            else: send_main_menu(f'❓ Comando sconosciuto: <code>{esc(cmd)}</code>')
    except Exception as e:
        log.exception('handle_command error'); send_message(f'❌ Errore: {esc(e)}')

def handle_callback(cb):
    cid=cb['id']; data=cb.get('data',''); answer_callback(cid)
    try:
        if data=='menu:main': send_main_menu()
        elif data=='menu:screen': send_screen_menu()
        elif data=='menu:audio': send_audio_menu()
        elif data=='menu:comm': send_comm_menu()
        elif data=='menu:files': send_files_menu()
        elif data=='menu:system': send_system_menu()
        elif data=='menu:clip': send_clip_menu()
        elif data=='menu:shell': send_shell_menu()
        elif data.startswith('cmd:'):
            mapping={'help':'/help','status':'/status','screenshot':'/screenshot','audiodevices':'/audiodevices','browse':'/browse','upload_here':'/uploadhere','pwd':'/pwd','clipboard':'/clipboard','processes':'/processes','lock':'/lock','sleep':'/sleep','chat_open':'/chat Chat aperta da Telegram.'}
            c=data[4:]
            if c in mapping: handle_command(mapping[c])
        elif data.startswith('screenrec:'): handle_command('/screenrec '+data.split(':',1)[1])
        elif data.startswith('record:'): handle_command('/record '+data.split(':',1)[1])
        elif data=='live:start': handle_command('/live start')
        elif data=='live:stop': handle_command('/live stop')
        elif data.startswith('browse:'): send_file_browser(data[7:])
        elif data.startswith('b:'):
            rest = data[2:]
            if ':' in rest:
                key, pg = rest.rsplit(':', 1)
                send_file_browser(path_from_key(key), int(pg) if pg.isdigit() else 0)
            else:
                send_file_browser(path_from_key(rest), 0)
        elif data.startswith('path:'):
            p = path_from_key(data[5:])
            send_message(f'📌 Percorso corrente:\n<code>{esc(p)}</code>')
        elif data.startswith('u:'):
            global _upload_target_dir
            p=path_from_key(data[2:])
            if p.exists() and p.is_dir():
                _upload_target_dir=p
                send_message(f'📤 Manda ora un file a questo bot. Lo salvo in:\n<code>{esc(p)}</code>')
            else:
                send_message('❌ Cartella non valida.')
        elif data.startswith('audioset:'): set_audio_device(data.split(':',1)[1])
        elif data.startswith('d:'):
            p=path_from_key(data[2:]); send_document(p,p.name) if p.exists() and p.is_file() else send_message('❌ File non trovato.')
        elif data.startswith('dl:'):
            p=Path(data[3:]); send_document(p,p.name) if p.exists() and p.is_file() else send_message('❌ File non trovato.')
        elif data.startswith('ask:'):
            a=data[4:]; prompts={'popup':'✍️ Scrivi il testo da mostrare nel popup.','chat':'✍️ Scrivi il messaggio da inviare nella chat sul PC.','search':'🔎 Scrivi il nome/file da cercare.','download':'⬇️ Scrivi il percorso completo del file da scaricare.','setclipboard':'✍️ Scrivi il testo da mettere nella clipboard.','openurl':'🌐 Scrivi URL da aprire.','shutdown':'⚠️ Scrivi la password admin per spegnere.','restart':'⚠️ Scrivi la password admin per riavviare.','logout':'⚠️ Scrivi la password admin per logout.','shell':'⚙️ Scrivi: password spazio comando PowerShell.','cmd':'⚙️ Scrivi: password spazio comando CMD.'}; ask(a,prompts.get(a,'Scrivi il valore.'))
    except Exception as e: log.exception('callback error'); send_message(f'❌ Callback error: {esc(e)}')

def handle_incoming_file(message):
    doc=message.get('document') or message.get('audio') or message.get('video')
    if not doc and message.get('photo'): doc=message['photo'][-1]
    if not doc: return False
    file_id=doc.get('file_id')
    file_name=doc.get('file_name') or f'file_{int(time.time())}'
    file_size=int(doc.get('file_size') or 0)
    caption=message.get('caption','')
    dest_dir=_upload_target_dir
    if '[dest:' in caption:
        try: dest_dir=Path(caption.split('[dest:',1)[1].split(']',1)[0].strip()); dest_dir.mkdir(parents=True,exist_ok=True)
        except Exception: pass
    dest=dest_dir/file_name
    if file_size and file_size > 20 * 1024 * 1024:
        mb = file_size / (1024 * 1024)
        send_message(
            f'<b>File troppo grande per il download via Bot API standard.</b>\n'
            f'<code>{esc(file_name)}</code> — {mb:.1f} MB\n\n'
            f'Il limite di Telegram Bot API per i download e circa <b>20 MB</b>.\n'
            f'Soluzioni:\n'
            f'- Invia tramite Google Drive / Dropbox / WeTransfer e apri il link dal PC\n'
            f'- Usa Local Bot API Server (nessun limite)\n'
            f'- Comprimi o suddividi il file prima di inviarlo'
        )
        return True
    try:
        download_file(file_id, dest)
    except Exception as e:
        log.exception('handle_incoming_file error')
        send_message(
            f'Download non riuscito: <code>{esc(file_name)}</code>\n\n'
            f'<code>{esc(str(e)[:500])}</code>'
        )
    return True

def monitor_usb():
    prev=set(p.device for p in psutil.disk_partitions())
    while True:
        time.sleep(3); cur=set(p.device for p in psutil.disk_partitions())
        for d in cur-prev: send_message(f'🔌 USB collegata: <code>{esc(d)}</code>')
        for d in prev-cur: send_message(f'⏏️ USB rimossa: <code>{esc(d)}</code>')
        prev=cur
def monitor_network():
    global _network_last_ip
    _network_last_ip=get_local_ip()
    while True:
        time.sleep(15); cur=get_local_ip()
        if cur!=_network_last_ip: send_message(f'🌐 IP cambiato: <code>{esc(_network_last_ip)}</code> → <code>{esc(cur)}</code>'); _network_last_ip=cur

def polling_loop():
    global last_offset
    log.info('Long polling avviato')
    while True:
        try:
            resp=tg_get('getUpdates', {'offset':last_offset,'timeout':25,'allowed_updates':['message','callback_query']})
            for upd in resp.get('result',[]):
                last_offset=upd['update_id']+1
                if 'callback_query' in upd:
                    cb=upd['callback_query']
                    if int(cb.get('from',{}).get('id',0))==AUTHORIZED_ID: threading.Thread(target=handle_callback,args=(cb,),daemon=True).start()
                    else: answer_callback(cb['id'],'⛔ Non autorizzato')
                    continue
                msg=upd.get('message') or upd.get('edited_message')
                if not msg: continue
                cid=int(msg.get('chat',{}).get('id',0))
                if cid!=AUTHORIZED_ID: tg_post('sendMessage', json={'chat_id':cid,'text':'⛔ Non autorizzato.'}); continue
                if handle_incoming_file(msg): continue
                t=msg.get('text','').strip()
                if t: log.info(f'Input: {t}'); threading.Thread(target=handle_command,args=(t,msg),daemon=True).start()
        except Exception as e: log.exception(f'Polling error: {e}'); time.sleep(5)

def main():
    log.info('PCMonitor v4.1 avviato')
    send_message(f'🟢 <b>PC acceso</b>\n👤 Utente: <code>{esc(os.environ.get("USERNAME","N/D"))}</code>\n🌐 IP: <code>{esc(get_local_ip())}</code>\n⏱ {datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')
    send_menu_keyboard_hint()
    send_main_menu('🤖 PCMonitor v4.1 pronto. Menu principale:')
    if CFG.get('monitoring',{}).get('usb_notifications',False): threading.Thread(target=monitor_usb,daemon=True).start()
    if CFG.get('monitoring',{}).get('network_change_notifications',False): threading.Thread(target=monitor_network,daemon=True).start()
    threading.Thread(target=polling_loop,daemon=True).start(); _tk_main_thread()
if __name__=='__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        boot_fail('main', e)
