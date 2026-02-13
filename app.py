# ============================================================================
#                                 GAME HUB PRO
#                         CENTRAL APPLICATION ENGINE
# ============================================================================
# Version: 5.0.0 (Full Master Production Build)
# Author: Zorkosss
# Architecture: Windows x64 (Optimized)
# ----------------------------------------------------------------------------
# This module serves as the primary backbone for Game Hub. It manages:
# 1.  Flask / Socket.io Server: High-frequency data transmission.
# 2.  SQLite Database: Persistent metadata storage with WAL concurrency.
# 3.  Native Windows API: Shell32/User32 bridge for file/folder dialogs.
# 4.  IDM Pro Downloader: High-speed parallel throughput management.
# 5.  yt-dlp Integration: Real-time video quality resolution.
# 6.  Process Tracking: Telemetry for active game executables.
# 7.  Security: Cryptographic signing for remote updates.
# 8.  System Peripherals: PS4/HID controller virtualization.
# ============================================================================

import sys
import os
import time
import logging
import json
import threading
import requests
import subprocess
import psutil
import sqlite3
import uuid
import webbrowser
import urllib.parse
import ctypes
import shutil
import zipfile
import webview
import io
import ctypes
from datetime import datetime
from ctypes import wintypes
from packaging import version
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO
from concurrent.futures import ThreadPoolExecutor
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature
from pypresence import Presence 
from flask_cors import CORS
import pystray
from PIL import Image
import engineio.async_drivers.threading

# Custom Local Module Imports
from game_scanner import GameScanner
from game import Game
import ps4_bridge 
# import downloader
import aria2_download_manager
import aria2_manager
from optimizer import optimizer_engine


# ============================================================================
# [1] DIRECTORY & PATHING CONFIGURATION
# ============================================================================

try:
    myappid = 'ziadnahouli.gamehub.pro.v4' # A unique string ID
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception as e:
    print(f"Taskbar ID failed: {e}")

def initialize_environment():
    """
    Sets up the persistent storage environment in LocalAppData.
    Ensures that log files and databases have a home.
    """
    if os.name == 'nt': 
        path = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub')
    else: 
        path = os.path.expanduser('~/Game Hub')

    if not os.path.exists(path): 
        try:
            os.makedirs(path)
        except Exception as e:
            print(f"CRITICAL: Failed to create data directory: {e}")
            sys.exit(1)
    return path

DATA_DIR = initialize_environment()

# Static File Paths
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
DATABASE_FILE = os.path.join(DATA_DIR, 'library.db')
LOG_FILE = os.path.join(DATA_DIR, 'app_runtime.log')
ERR_LOG_FILE = os.path.join(DATA_DIR, 'app_error.log')
COVERS_DIR = os.path.join(DATA_DIR, 'covers')
if not os.path.exists(COVERS_DIR):
    os.makedirs(COVERS_DIR)

# Global Constants
STEAMGRIDDB_API_URL = "https://www.steamgriddb.com/api/v2"
STEAM_API_BASE = "https://api.steampowered.com"
GITHUB_REPO = "Ziadnahouli/GameHub"
EXTENSION_VERSION_URL = "https://raw.githubusercontent.com/Ziadnahouli/GameHub/main/extension_version.json"
EXTENSION_RELOAD_SIGNAL = False
VERSION_TO_IGNORE = ""
EXTENSION_RELOAD_TIME = None  # Timestamp when reload signal was sent
EXTENSION_ZIP_URL = "https://github.com/Ziadnahouli/GameHub/raw/main/extension.zip"
CURRENT_VERSION = "4.2" 
LAST_SEEN_EXT_VERSION = "0.0"
EXTENSION_CHECK_GRACE_PERIOD = 6  # Seconds to wait before showing "not installed" lock
EXTENSION_RELOAD_GRACE_PERIOD = 8  # Seconds to wait after reload before showing version mismatch lock
DATABASE_SCHEMA_VERSION = 8 
SERVER_PORT = 5000
HOST_URL = f"http://127.0.0.1:{SERVER_PORT}"
DISCORD_APP_ID = "1328467982635925565"

# ============================================================================
# [2] LOGGING INFRASTRUCTURE
# ============================================================================

def setup_master_logging():
    """
    Configures the global logging system. 
    Maintains a rotation-ready log file and standard output stream.
    """
    log_format = '%(asctime)s - [%(levelname)s] - %(name)s - %(message)s'
    
    # Initialize basic config
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Silence third-party noise
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    logging.getLogger('engineio').setLevel(logging.ERROR)
    logging.getLogger('socketio').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.ERROR)

setup_master_logging()
setup_master_logging()
logger = logging.getLogger("GameHub_Core")

class InterceptionLogger:
    def __init__(self):
        self.logs = [] # Keep last 50 decisions

    def log(self, url, decision, reason):
        entry = {
            "time": time.time(),
            "url": url[:100],
            "decision": decision, # "INTERCEPTED" or "IGNORED"
            "reason": reason      # "Archive Extension", "Sensitive Domain", etc
        }
        self.logs.insert(0, entry)
        if len(self.logs) > 50: self.logs.pop()

intercept_log = InterceptionLogger()



# ============================================================================
# [3] NATIVE WINDOWS API (64-BIT HARDENED CTYPES)
# ============================================================================

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32
ole32 = ctypes.windll.ole32
comdlg32 = ctypes.windll.comdlg32

# Define return types (64-bit addresses)
shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
comdlg32.GetOpenFileNameW.restype = wintypes.BOOL

# Define argument types (Prevents OverflowError)
shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CoInitialize.argtypes = [ctypes.c_void_p]

class BROWSEINFO(ctypes.Structure):
    """Native Windows Structure for Directory Selection Dialogs."""
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int)
    ]

class OPENFILENAMEW(ctypes.Structure):
    """Native Windows Structure for File Selection Dialogs."""
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustomFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD)
    ]

# ============================================================================
# [4] DATABASE ARCHITECTURE & MIGRATION
# ============================================================================

def get_db_connection():
    """
    Creates a thread-safe connection to the SQLite local database.
    Enables WAL mode for concurrent read/write operations during scanning.
    """
    try:
        conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=OFF;")
        conn.execute("PRAGMA cache_size=-64000;")
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"[Database] Connection Error: {e}")
        return None

def init_db():
    """
    Creates the core tables if they are missing from the environment.
    """
    conn = get_db_connection()
    if not conn: return

    cursor = conn.cursor()
    
    # Games Metadata Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY, 
            name TEXT, 
            source TEXT, 
            launch_id TEXT, 
            install_path TEXT,
            favorite BOOLEAN DEFAULT 0, 
            hidden BOOLEAN DEFAULT 0, 
            last_played REAL DEFAULT 0, 
            playtime_seconds INTEGER DEFAULT 0,
            grid_image_url TEXT DEFAULT '', 
            avg_fps TEXT DEFAULT '', 
            best_ping TEXT DEFAULT '', 
            launch_args TEXT DEFAULT '',
            controller_type TEXT DEFAULT 'None', 
            auto_bridge BOOLEAN DEFAULT 0, 
            focus_mode BOOLEAN DEFAULT 0, 
            tags TEXT DEFAULT '[]'
        )''')
    
    # Game Session History
    cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            game_id TEXT, 
            start_time REAL, 
            duration INTEGER
        )''')
    
    # Application Version Management
    cursor.execute('CREATE TABLE IF NOT EXISTS meta (version INTEGER)')
    
    # Aria2 Tasks Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS aria2_tasks (
            task_id TEXT PRIMARY KEY,
            gid TEXT NOT NULL,
            url TEXT NOT NULL,
            save_dir TEXT NOT NULL,
            filename TEXT DEFAULT '',
            status TEXT DEFAULT 'DOWNLOADING',
            total_bytes INTEGER DEFAULT 0,
            downloaded_bytes INTEGER DEFAULT 0,
            created_ts REAL,
            updated_ts REAL
        )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_aria2_gid ON aria2_tasks(gid)')

    # Optimizer Snapshots Table
    cursor.execute('''CREATE TABLE IF NOT EXISTS optimizer_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            unique_id TEXT NOT NULL,
            created_ts REAL,
            payload_json TEXT
        )''')
    
    conn.commit()
    conn.close()
    logger.info("[Database] Logical schema initialized.")

def check_and_update_db_schema():
    """
    Performs migrations to sync the local DB with the current code requirements.
    """
    conn = get_db_connection()
    if not conn: return

    cursor = conn.cursor()
    cursor.execute('SELECT version FROM meta')
    row = cursor.fetchone()
    current_db_version = row['version'] if row else 0
    
    if current_db_version < DATABASE_SCHEMA_VERSION:
        logger.info(f"[Database] Upgrading system metadata to v{DATABASE_SCHEMA_VERSION}...")
        
        cursor.execute("PRAGMA table_info(games)")
        existing_cols = [r['name'] for r in cursor.fetchall()]
        
        # New feature columns
        definitions = {
            'avg_fps': 'TEXT', 'best_ping': 'TEXT', 'launch_args': 'TEXT', 
            'controller_type': 'TEXT', 'auto_bridge': 'BOOLEAN', 
            'focus_mode': 'BOOLEAN', 'tags': 'TEXT',
            'installed_version': 'TEXT', 'installed_build': 'TEXT',
            'last_updated': 'REAL', 'update_status': 'TEXT',
            'update_source': 'TEXT', 'last_update_check': 'REAL'
        }
        
        for col, col_type in definitions.items():
            if col not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE games ADD COLUMN {col} {col_type}")
                    logger.info(f"[Database] Migration: Expansion of '{col}' complete.")
                except Exception as e:
                    logger.error(f"[Database] Failed to add column {col}: {e}")
            
        cursor.execute('DELETE FROM meta')
        cursor.execute('INSERT INTO meta (version) VALUES (?)', (DATABASE_SCHEMA_VERSION,))
        
        # Migrations for aria2_tasks
        try:
            cursor.execute("PRAGMA table_info(aria2_tasks)")
            existing_aria_cols = [r['name'] for r in cursor.fetchall()]
            if 'total_bytes' not in existing_aria_cols:
                cursor.execute("ALTER TABLE aria2_tasks ADD COLUMN total_bytes INTEGER DEFAULT 0")
            if 'downloaded_bytes' not in existing_aria_cols:
                cursor.execute("ALTER TABLE aria2_tasks ADD COLUMN downloaded_bytes INTEGER DEFAULT 0")
        except Exception as e:
            logger.error(f"[Database] Failed to migrate aria2_tasks: {e}")

        conn.commit()
    
    conn.close()

# ============================================================================
# [5] CORE APP LOGIC & CONFIGURATION
# ============================================================================

def load_config():
    """
    Loads the global application state from the config JSON.
    """
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        else:
            # Create a blank default config
            default = {
                "theme": "default", 
                "scan_paths": [],
                "default_splits": 16,
                "default_connections": 16,
                "default_speed_limit": 0
            }
            save_config(default)
            return default
    except Exception as e:
        logger.error(f"[Config] Failure reading config: {e}")
        return {}

def save_config(config):
    """
    Writes the global application state to disk.
    """
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"[Config] Failure writing config: {e}")

# ============================================================================
# [6] SECURITY & UPDATE VERIFICATION
# ============================================================================

def load_rsa_key():
    """
    Locates and reads the RSA-2048 public key.
    Supports both standard Python execution and PyInstaller (.exe) environments.
    """
    try:
        # Determine if running as a compiled EXE or a script
        if getattr(sys, 'frozen', False):
            # If EXE, look in the temporary internal folder
            base_path = sys._MEIPASS
        else:
            # If script, look in the current folder
            base_path = os.path.dirname(os.path.abspath(__file__))
            
        key_path = os.path.join(base_path, "public_key.pem")
        
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                # Return the raw bytes of the key
                return f.read().strip()
        else:
            logging.error(f"Security Error: 'public_key.pem' not found at {key_path}")
            return b"KEY_NOT_FOUND"
    except Exception as e:
        logging.error(f"Security Error: Failed to load RSA key: {e}")
        return b"ERROR_LOADING"

# Initialize the global constant used by the updater
RSA_PUBLIC_KEY = load_rsa_key()

# ============================================================================
# [7] SYSTEM PERFORMANCE & TELEMETRY
# ============================================================================

def calculate_latency():
    """
    Executes an ICMP echo request to measure real-time network stability.
    """
    try:
        # Ping Google DNS
        cmd = "ping -n 1 8.8.8.8"
        process = subprocess.run(cmd, capture_output=True, shell=True, text=True, timeout=2)
        
        if "time=" in process.stdout:
            # Extract ms value
            return process.stdout.split("time=")[1].split("ms")[0].strip()
        return "--"
    except Exception:
        return "Err"

def configure_os_power(performance_mode=True):
    """
    Optimizes the Windows Power Plan for active gaming sessions.
    """
    try:
        # Standard Windows GUIDs for power schemes
        scheme = 'scheme_min' if performance_mode else 'scheme_balanced'
        subprocess.run(f'powercfg /setactive {scheme}', shell=True, capture_output=True)
        logger.info(f"[System] Power scheme synchronized: {scheme}")
    except Exception as e:
        logger.warning(f"[System] Power optimization failed: {e}")

def bring_window_to_front():
    """
    Brings the Game Hub window to the front using Windows API.
    """
    global GLOBAL_WINDOW
    if not GLOBAL_WINDOW:
        return
    
    try:
        # Get the window handle from webview
        hwnd = GLOBAL_WINDOW.hwnd if hasattr(GLOBAL_WINDOW, 'hwnd') else None
        
        if hwnd:
            # Windows API constants
            SW_RESTORE = 9
            HWND_TOP = 0
            SWP_SHOWWINDOW = 0x0040
            
            # Restore if minimized
            user32.ShowWindow(hwnd, SW_RESTORE)
            # Bring to front
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_SHOWWINDOW)
        else:
            # Fallback: Try to find window by title using process name
            try:
                # Get current process ID
                current_pid = os.getpid()
                
                # Find window by process
                def find_window_by_pid(pid):
                    found_hwnd = [None]  # Use list for mutable closure
                    
                    def enum_proc(hwnd, lParam):
                        process_id = ctypes.c_ulong()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                        if process_id.value == pid:
                            window_text = ctypes.create_unicode_buffer(512)
                            user32.GetWindowTextW(hwnd, window_text, 512)
                            if "Game Hub" in window_text.value:
                                found_hwnd[0] = hwnd
                                return False  # Stop enumeration
                        return True
                    
                    # Define EnumWindows signature
                    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
                    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
                    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
                    return found_hwnd[0]
                
                hwnd = find_window_by_pid(current_pid)
                
                if hwnd:
                    SW_RESTORE = 9
                    HWND_TOP = 0
                    SWP_SHOWWINDOW = 0x0040
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                    user32.BringWindowToTop(hwnd)
                    user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_SHOWWINDOW)
            except Exception as fallback_err:
                logger.debug(f"[Window] Fallback focus failed: {fallback_err}")
    except Exception as e:
        logger.debug(f"[Window] Focus operation failed: {e}")

# ============================================================================
# [8] PLAYTIME & PROCESS TRACKING ENGINE
# ============================================================================

class PlaytimeTracker(threading.Thread):
    """
    High-fidelity process monitor. 
    Manages playtime logging, power plans, and Discord Rich Presence.
    """
    def __init__(self, game, quick_scan=False, initial_pid=None):
        super().__init__(daemon=True)
        self.game = game
        self.quick_scan = quick_scan
        self.initial_pid = initial_pid
        self.game_name = game.name
        self.source = game.source
        self.install_path = os.path.normpath(game.install_path) if game.install_path else None
        self.stop_event = threading.Event()
        self.tracked_pids = set()
        self.rpc_client = None
        self.active_pid = None # New public property for Optimizer

    def run(self):
        global CURRENT_RUNNING_GAME
        session_start = time.time()
        
        # 1. Wait for executable to initialize
        target_pid = self.initial_pid if self.initial_pid else self.resolve_active_pid(quick_scan=self.quick_scan)
        if not target_pid: 
            logger.warning(f"[Tracker] Aborting: Process '{self.game_name}' not found.")
            return

        self.active_pid = target_pid # Store for external access
        optimizer_engine.current_pid = target_pid
        unique_id = f"{self.source}|{self.game_name}"
        # Include PID in broadcast so frontend knows it source of truth
        CURRENT_RUNNING_GAME = {'name': self.game_name, 'source': self.source, 'unique_id': unique_id, 'pid': target_pid}
        socketio.emit('game_started', CURRENT_RUNNING_GAME)

        # 2. Performance Optimization
        if self.game.focus_mode:
            configure_os_power(True)
            try: 
                psutil.Process(target_pid).nice(psutil.HIGH_PRIORITY_CLASS)
            except Exception: pass

        # 3. Discord RPC Hook
        try:
            self.rpc_client = Presence(DISCORD_APP_ID)
            self.rpc_client.connect()
            self.rpc_client.update(
                state=f"Playing {self.game_name}", 
                start=session_start, 
                large_image="app_logo"
            )
        except Exception: pass

        # 4. Monitoring Loop
        self.tracked_pids.add(target_pid)
        while not self.stop_event.is_set():
            time.sleep(5)
            if not self.check_process_liveness(): 
                break
        
        # 5. Cleanup & Persistence
        total_seconds = int(time.time() - session_start)
        self.persist_session_data(total_seconds)
        
        if self.game.focus_mode: 
            configure_os_power(False)
        if self.rpc_client: 
            self.rpc_client.close()
        if self.game.auto_bridge:
            ps4_bridge.bridge.stop()

        CURRENT_RUNNING_GAME = None
        optimizer_engine.current_pid = None
        socketio.emit('game_stopped', {'name': self.game_name, 'source': self.source})
        load_games_from_db()

    def resolve_active_pid(self, quick_scan=False):
        """Identifies the PID of the game with launcher filtering."""
        ignore = ['steam.exe', 'epicgameslauncher.exe', 'ea.exe', 'origin.exe', 'galaxyclient.exe']
        clean_name = self.game_name.lower().replace(" ", "")
        
        # If quick_scan (auto-detect), only try once. If launching, retry for slow load.
        retries = 1 if quick_scan else 40
        
        for _ in range(retries):
            for p in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    p_name = p.info['name'].lower()
                    p_exe = p.info['exe']
                    
                    if p_name in ignore: continue
                    
                    # Match by path
                    if p_exe and self.install_path and self.install_path != "Unknown":
                        if self.install_path.lower() in p_exe.lower(): 
                            return p.info['pid']
                    
                    # Match by normalized name
                    if clean_name in p_name.replace(".exe", ""): 
                        return p.info['pid']
                except (psutil.NoSuchProcess, psutil.AccessDenied): 
                    continue
            if not quick_scan: time.sleep(2)
        return None

    def check_process_liveness(self):
        """Monitors child processes and the main process for exit signals."""
        still_alive = False
        for pid in list(self.tracked_pids):
            try:
                proc = psutil.Process(pid)
                if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                    still_alive = True
                    # Add new children to the watch list
                    for child in proc.children(recursive=True):
                        self.tracked_pids.add(child.pid)
            except Exception: 
                self.tracked_pids.discard(pid)
        return still_alive

    def persist_session_data(self, duration):
        """Saves session duration to the SQLite library."""
        if duration < 10: return 
        uid = f"{self.source}|{self.game_name}"
        conn = get_db_connection()
        if not conn: return
        
        try:
            conn.execute("INSERT INTO sessions (game_id, start_time, duration) VALUES (?, ?, ?)", 
                         (uid, time.time(), duration))
            conn.execute("UPDATE games SET playtime_seconds = playtime_seconds + ?, last_played = ? WHERE id = ?", 
                         (duration, time.time(), uid))
            conn.commit()
        except Exception as e:
            logger.error(f"[Tracker] Persistence Error: {e}")
        finally:
            conn.close()

# ============================================================================
# [9] FLASK WEB INFRASTRUCTURE
# ============================================================================
if getattr(sys, 'frozen', False):
    # Path when running as a compiled .EXE
    ROOT_DIR = sys._MEIPASS
else:
    # Path when running as a .PY script
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__, 
    template_folder=os.path.join(ROOT_DIR, 'templates'),
    static_folder=os.path.join(ROOT_DIR, 'static'),
    static_url_path='/static'
)

COVERS_DIR = os.path.join(DATA_DIR, 'covers')

app.config['SECRET_KEY'] = os.environ.get("GAMEHUB_SECRET_KEY", str(uuid.uuid4()))
CORS(app, supports_credentials=True, resources={r"/api/*": {"origins": "*"}})



socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

@socketio.on('connect')
def handle_connect():
    global CURRENT_RUNNING_GAME
    print(f"[Socket] Client connected. Active Game: {CURRENT_RUNNING_GAME['name'] if CURRENT_RUNNING_GAME else 'None'}")
    
    if CURRENT_RUNNING_GAME:
        emit('game_started', CURRENT_RUNNING_GAME)
    else:
        # If no game known, try scanning again (maybe it started while we were loading)
        # Run in thread to not block the socket ack
        threading.Thread(target=scan_for_running_games, daemon=True).start()

optimizer_engine.set_socketio(socketio)

# Initialize aria2 manager (Shell only - start() moved to __main__ after DB init)
aria2_dl = aria2_download_manager.Aria2DownloadManager(DATABASE_FILE, socketio)

# Application Runtime Globals
scanner = GameScanner()
all_games = []
CURRENT_TRACKER = None
CURRENT_RUNNING_GAME = None
GLOBAL_WINDOW = None  # Webview window reference for focus operations


@app.route('/')
def route_index():
    """Renders the primary application dashboard."""
    return render_template('index.html', v=time.time())

@app.route('/api/games')
def route_api_get_games():
    """Endpoint for full library serialization."""
    return jsonify([game.to_dict() for game in all_games])

@app.route('/api/refresh', methods=['POST'])
def route_api_manual_refresh():
    """Manual trigger for the library scanning background task."""
    threading.Thread(target=scan_library_task, daemon=True).start()
    return jsonify({"status": "success"})

@app.route('/api/launch', methods=['POST'])
def route_api_launch():
    """
    Executes the appropriate shell command to launch a game.
    Initializes telemetry tracking upon success.
    """
    global CURRENT_TRACKER
    payload = request.json
    name, source = payload.get('name'), payload.get('source')
    
    # Locate game in memory
    game_obj = next((g for g in all_games if g.name == name and g.source == source), None)
    
    if not game_obj: 
        return jsonify({"status": "error", "message": "Metadata missing for this entry."}), 404

    try:
        launch_cmd = game_obj.get_launch_command()
        
        # Dispatch Shell Commands
        if source == 'Steam':
            os.startfile(launch_cmd)
        elif source in ['Epic Games', 'EA']:
            subprocess.Popen(f'cmd /c start "" "{launch_cmd}"', shell=True)
        else:
            # Manual Executable Path
            if os.path.exists(launch_cmd):
                subprocess.Popen(launch_cmd, cwd=os.path.dirname(launch_cmd), shell=True)
            else:
                os.startfile(launch_cmd)

        # Handle Tracker Lifecycle
        if CURRENT_TRACKER and CURRENT_TRACKER.is_alive():
            CURRENT_TRACKER.stop_event.set()
        
        CURRENT_TRACKER = PlaytimeTracker(game_obj)
        CURRENT_TRACKER.start()

        # Conditional Hardware Bridge
        if game_obj.auto_bridge:
            ps4_bridge.bridge.start()

        return jsonify({"status": "success"})
    except Exception as launch_err:
        logger.error(f"[Launch] Sequence Failed: {launch_err}")
        return jsonify({"status": "error", "message": str(launch_err)}), 500



@app.route('/api/update_game', methods=['POST'])
def route_api_update_game():
    """Updates non-system game metadata (Favorites, Art, Hidden status)."""
    payload = request.json
    unique_id = f"{payload.get('source')}|{payload.get('name')}"
    data_map = payload.get('update_data')
    
    db_conn = get_db_connection()
    if not db_conn: return jsonify({"status": "error"})
    
    try:
        cursor = db_conn.cursor()
        for key, value in data_map.items():
            # Standardized update logic
            query = f"UPDATE games SET {key} = ? WHERE id = ?"
            cursor.execute(query, (value, unique_id))
        db_conn.commit()
        load_games_from_db() # Sync memory
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"[Metadata] Update failed: {e}")
        return jsonify({"status": "error", "message": str(e)})
    finally:
        db_conn.close()


# ============================================================================
# [10] IDM PRO & VIDEO PROCESSING
# ============================================================================

# ============================================================================
# [11] AI OPTIMIZER ROUTES
# ============================================================================

@app.route('/api/optimizer/benchmark/start', methods=['POST'])
def route_opt_start():
    # Expects: { unique_id, target_mode, pid (optional), exe_path (optional) }
    # specific pid logic override for safety if provided, else use active tracker
    payload = request.json
    uid = payload.get('unique_id')
    mode = payload.get('target_mode', 'Balanced')
    
    # Resolve PID
    pid = payload.get('pid')
    path = payload.get('exe_path')

    # Fallback to active tracker if not provided
    global CURRENT_TRACKER
    if not pid and CURRENT_TRACKER and CURRENT_TRACKER.is_alive():
         # extraction logic from tracker (needs tracker to expose pid)
         # For now, we rely on the frontend sending the right PID from socket data
         pass

    if not pid:
         return jsonify({"status": "error", "message": "Game process not identified."})

    res = optimizer_engine.start_benchmark(uid, pid, path, mode)
    return jsonify(res)

@app.route('/api/optimizer/benchmark/stop', methods=['POST'])
def route_opt_stop():
    payload = request.json
    uid = payload.get('unique_id')
    res = optimizer_engine.stop_benchmark(uid)
    return jsonify(res)

@app.route('/api/optimizer/benchmark/status', methods=['GET'])
def route_opt_status():
    uid = request.args.get('unique_id')
    return jsonify(optimizer_engine.get_status(uid))

@app.route('/api/optimizer/apply', methods=['POST'])
def route_opt_apply():
    payload = request.json
    uid = payload.get('unique_id')
    actions = payload.get('actions', [])
    dry = payload.get('dry_run', False)
    
    res = optimizer_engine.apply_optimizations(uid, actions, dry_run=dry)
    
    # If not dry run and successful, save snapshot
    if not dry and res.get('status') == 'success':
        try:
            diffs = res.get('diffs')
            conn = get_db_connection()
            if conn:
                snapshot_id = str(uuid.uuid4())
                conn.execute("INSERT INTO optimizer_snapshots (snapshot_id, unique_id, created_ts, payload_json) VALUES (?, ?, ?, ?)",
                             (snapshot_id, uid, time.time(), json.dumps(diffs)))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f"[Optimizer] Snapshot save failed: {e}")
        
    return jsonify(res)

@app.route('/api/optimizer/restore', methods=['POST'])
def route_opt_restore():
    payload = request.json
    uid = payload.get('unique_id')
    
    conn = get_db_connection()
    if not conn: return jsonify({"status": "error", "message": "DB unavailable"})
    
    try:
        cursor = conn.cursor()
        # Find latest snapshot
        cursor.execute("SELECT payload_json FROM optimizer_snapshots WHERE unique_id = ? ORDER BY created_ts DESC LIMIT 1", (uid,))
        row = cursor.fetchone()
        
        if not row:
            return jsonify({"status": "error", "message": "No snapshots found to restore."})
            
        diffs = json.loads(row['payload_json'])
        res = optimizer_engine.restore_state(diffs)
        
        # Optional: delete snapshot after restore? Or keep history? Keeping for now.
        return jsonify(res)
    except Exception as e:
        logger.error(f"[Optimizer] Restore failed: {e}")
        return jsonify({"status": "error", "message": str(e)})
    finally:
        conn.close()

@app.route('/api/optimizer/overlay/toggle', methods=['POST'])
def route_opt_overlay_toggle():
    payload = request.json
    enable = payload.get('enabled', False)
    res = optimizer_engine.toggle_overlay(enable)
    return jsonify(res)

@app.route('/api/video/formats', methods=['POST'])
def route_api_video_formats():
    """Endpoint for fetching video format options from yt-dlp."""
    payload = request.get_json(silent=True) or {}
    target_url = payload.get('url')
    
    if not target_url: 
        return jsonify({"status": "error", "message": "URL Required"})

    logger.info(f"[Video] Fetching formats for: {target_url}")

    try:
        import yt_dlp
        opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            metadata = ydl.extract_info(target_url, download=False)
            raw_formats = metadata.get('formats', [])
            
            output_list = []
            for entry in raw_formats:
                px = entry.get('height')
                # Filter for combined formats or high-quality video streams
                if px and px >= 360 and entry.get('vcodec') != 'none':
                    size_raw = entry.get('filesize') or entry.get('filesize_approx') or 0
                    mb_val = f"{round(size_raw / (1024*1024), 1)} MB" if size_raw > 0 else "Unknown"
                    
                    output_list.append({
                        "id": entry.get('format_id'),
                        "label": f"{px}p - {entry.get('ext')} ({mb_val})",
                        "height": px,
                        "size": size_raw
                    })

            # Remove duplicates and sort by quality
            deduped = {f['label']: f for f in output_list}
            final = sorted(deduped.values(), key=lambda x: (x['height'], x['size']), reverse=True)

            return jsonify({
                "status": "success", 
                "title": metadata.get('title', 'Video Content'), 
                "formats": final[:8]
            })
    except Exception as yt_err:
        logger.error(f"[Video] Resolution failed: {yt_err}")
        return jsonify({"status": "error", "message": str(yt_err)})

# ============================================================================
# [11] FILE SYSTEM & SHELL INTEGRATION
# ============================================================================

@app.route('/api/browse')
def route_api_browse():
    """Native Windows File Browser (64-bit safe)."""
    buffer_len = 1024
    mem_buffer = ctypes.create_unicode_buffer(buffer_len)
    
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = user32.GetForegroundWindow()
    ofn.lpstrFile = ctypes.cast(mem_buffer, wintypes.LPWSTR)
    ofn.nMaxFile = buffer_len
    ofn.lpstrFilter = "Executables (*.exe)\0*.exe\0All Files (*.*)\0*.*\0"
    ofn.Flags = 0x00080000 | 0x00001000 # Explorer + File Must Exist

    if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return jsonify({"status": "success", "path": os.path.normpath(mem_buffer.value)})
    return jsonify({"status": "cancelled"})

# app.py

@app.route('/api/browse_folder')
def api_browse_folder():
    """Fixed native folder browser with explicit pointer handling."""
    try:
        ole32.CoInitialize(None)
        
        bi = BROWSEINFO()
        bi.hwndOwner = user32.GetForegroundWindow()
        bi.lpszTitle = "Select Default Download Directory"
        bi.ulFlags = 0x00000001 | 0x00000040 # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
        
        # pidl is a 64-bit memory address
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        
        if pidl:
            path_buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if shell32.SHGetPathFromIDListW(pidl, path_buffer):
                result_path = os.path.normpath(path_buffer.value)
                ole32.CoTaskMemFree(pidl)
                ole32.CoUninitialize()
                return jsonify({"status": "success", "path": result_path})
            
            ole32.CoTaskMemFree(pidl)
        
        ole32.CoUninitialize()
        return jsonify({"status": "cancelled"})

    except Exception as e:
        logger.error(f"[Shell] Folder browse crashed: {e}")
        try: ole32.CoUninitialize()
        except: pass
        # Use str(e) to ensure the message is JSON serializable
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/open_folder', methods=['POST'])
def route_api_open_folder():
    """Opens a physical Windows path in the default shell explorer."""
    req_data = request.get_json()
    path_str = req_data.get('path')
    
    if path_str and os.path.exists(path_str):
        # Resolve folder if path points to a specific file
        target = path_str if os.path.isdir(path_str) else os.path.dirname(path_str)
        subprocess.Popen(f'explorer "{os.path.normpath(target)}"')
        return jsonify({"status": "success"})
    
    return jsonify({"status": "error", "message": "Directory unreachable."}), 404

@app.route('/api/perform_update', methods=['POST'])
def route_api_perform_update():
    """
    SECURE UPDATE ORCHESTRATOR:
    1. Downloads Installer and Signature from GitHub.
    2. Verifies integrity using RSA-2048.
    3. Deploys a 'Force-Kill' batch script to handle Windows file locks.
    4. Triggers the new installer in Silent mode.
    """
    data = request.get_json(silent=True) or {}
    download_url = data.get('url')
    
    if not download_url:
        return jsonify({"status": "error", "message": "Missing update URL."}), 400

    def run_update_sequence():
        try:
            # Workspace Setup
            temp_workspace = os.path.join(os.getenv('TEMP'), 'GameHub_Update_Pro')
            if not os.path.exists(temp_workspace):
                os.makedirs(temp_workspace)
            
            exe_target = os.path.join(temp_workspace, "Update_Installer.exe")
            sig_target = exe_target + ".sig"
            sig_url = download_url + ".sig"

            # Step A: Download the Installer
            logger.info(f"[Updater] Fetching installer: {download_url}")
            socketio.emit('update_progress', {'status': 'Downloading Update...', 'percent': 20})
            
            with requests.get(download_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(exe_target, 'wb') as f:
                    shutil.copyfileobj(r.raw, f)

            # Step B: Fetch the Developer's Signature
            socketio.emit('update_progress', {'status': 'Verifying Security...', 'percent': 50})
            sig_response = requests.get(sig_url, timeout=20)
            if not sig_response.ok:
                raise Exception("Security signature missing on GitHub. Update aborted.")
            
            with open(sig_target, 'wb') as f:
                f.write(sig_response.content)

            # Step C: Cryptographic Verification (RSA-2048)
            socketio.emit('update_progress', {'status': 'Checking Integrity...', 'percent': 75})
            
            if RSA_PUBLIC_KEY in [b"KEY_NOT_FOUND", b"ERROR_LOADING"]:
                raise Exception("Local RSA key missing. Verification impossible.")

            try:
                public_key_obj = serialization.load_pem_public_key(RSA_PUBLIC_KEY)
                
                with open(exe_target, 'rb') as f: exe_data = f.read()
                with open(sig_target, 'rb') as f: sig_data = f.read()

                public_key_obj.verify(
                    sig_data,
                    exe_data,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )
                logger.info("[Updater] RSA Signature Valid. Source is trusted.")
            except InvalidSignature:
                logger.critical("[Updater] SECURITY ALERT: REJECTED! Signature mismatch.")
                if os.path.exists(exe_target): os.remove(exe_target)
                socketio.emit('update_error', {'message': 'Verification Failed: Unrecognized Source!'})
                return

            # Step D: Deploy 'Nuclear' Batch Script (Fixes Access Denied / Code 5)
            socketio.emit('update_progress', {'status': 'Finalizing Update...', 'percent': 95})
            
            bat_file = os.path.join(temp_workspace, "deploy_update.bat")
            with open(bat_file, "w") as f:
                f.write(f'''@echo off
title Game Hub Pro - Performance Update
echo ---------------------------------------------------
echo SHUTTING DOWN GAME HUB COMPONENTS...
echo ---------------------------------------------------
:: Force kill the main process
taskkill /f /im app.exe > nul 2>&1
:: Force kill any stuck downloaders or resolvers
taskkill /f /im yt-dlp.exe > nul 2>&1

:: CRITICAL: Wait 5 seconds for Windows Kernel to release DLL handles
echo Waiting for system to unlock files...
timeout /t 5 /nobreak > nul

echo INSTALLING NEW VERSION...
start "" "{exe_target}" /SILENT
echo ---------------------------------------------------
echo UPDATE PROCESS DISPATCHED. THIS WINDOW WILL CLOSE.
exit
''')

            # Step E: Trigger Shutdown and Install
            logger.info("[Updater] Dispatching system-level update batch.")
            subprocess.Popen([bat_file], shell=True)
            
            # Force immediate exit of Python to ensure files are released
            os._exit(0)

        except Exception as err:
            logger.error(f"[Updater] Fatal Update Failure: {err}")
            socketio.emit('update_error', {'message': f"Update Engine Error: {str(err)}"})

    # Dispatch to background thread
    threading.Thread(target=run_update_sequence, daemon=True).start()
    
    return jsonify({"status": "success", "message": "Background update process active."})

# ============================================================================
# [13] HARDWARE MONITORING PIPELINE
# ============================================================================

@app.route('/api/system_stats')
def route_api_system_stats():
    """Provides current hardware telemetry for the UI dashboard."""
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "ping": calculate_latency()
    })

@app.route('/api/bridge/status')
def route_api_bridge_status():
    """Returns the connectivity and power state of the Sony HID bridge."""
    return jsonify({
        "running": ps4_bridge.bridge.running,
        "battery": ps4_bridge.bridge.get_battery()
    })

@app.route('/api/bridge/toggle', methods=['POST'])
def route_api_toggle_bridge():
    """Starts or stops the HID controller capture thread."""
    data = request.json
    command = data.get('enable')
    
    if command:
        active = ps4_bridge.bridge.start()
        return jsonify({"status": "success" if active else "error", "message": "Bridge Enabled" if active else "Hardware not found"})
    else:
        ps4_bridge.bridge.stop()
        return jsonify({"status": "success", "message": "Bridge Disabled"})

@app.route('/api/bridge/settings', methods=['POST'])
def route_api_bridge_settings():
    """Updates controller bridge settings and persists to config."""
    data = request.get_json(silent=True) or {}
    dz = data.get('deadzone')
    sens = data.get('sensitivity')
    try:
        if dz is not None and sens is not None:
            ps4_bridge.bridge.update_settings(float(dz), float(sens))
            cfg = load_config()
            cfg['controller_deadzone'] = float(dz)
            cfg['controller_sensitivity'] = float(sens)
            save_config(cfg)
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Missing parameters"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# [DOWNLOAD API] - ADD DOWNLOADS FROM EXTENSION
# ============================================================================

@app.route('/api/downloads/add', methods=['POST'])
def route_api_downloads_add():
    """
    Receives download requests from the browser extension.
    Cookie processing removed for security.
    """
    payload = request.get_json(silent=True) or {}
    url = payload.get('url')
    referer = payload.get('referer')
    format_id = payload.get('format_id')
    filename = payload.get('filename')
    size = payload.get('size')
    reason = payload.get('reason', 'Unknown')
    user_agent = payload.get('user_agent')
    
    # Advanced Options
    split = payload.get('split')
    connections = payload.get('connections')
    speed_limit = payload.get('speed_limit')
    save_path_override = payload.get('save_path')
    
    if not url:
        return jsonify({"status": "error", "message": "URL is required"}), 400
    
    # Log interception decision
    intercept_log.log(url, "INTERCEPTED", reason)
    
    try:
        # Get download path from config
        config = load_config()
        download_path = save_path_override or config.get('default_download_path')
        if not download_path:
            download_path = os.path.join(os.path.expanduser("~"), "Downloads")
        
        # Apply default settings from config if not provided
        if split is None:
            split = config.get('default_splits', 16)
        if connections is None:
            connections = config.get('default_connections', 16)
        if speed_limit is None:
            speed_limit_val = config.get('default_speed_limit', 0)
            if speed_limit_val > 0:
                speed_limit = str(speed_limit_val) + "M"
        
        # Build options (simplified for aria2)
        options = {}
        if referer:
            options['referer'] = referer
        if user_agent:
            options['user-agent'] = user_agent
        
        # Add advanced aria2 options if present
        if split: options['split'] = split
        if connections: options['max-connection-per-server'] = connections
        if speed_limit: options['max-download-limit'] = speed_limit
        
        # Queue the download via aria2
        result = aria2_dl.add(
            url=url,
            save_dir=download_path,
            filename=filename,
            options=options
        )
        
        if result.get('success'):
            logger.info(f"[Aria2] Queued download: {filename or url[:50]} (Reason: {reason})")
            
            # Bring window to front when download starts
            try:
                bring_window_to_front()
            except Exception as e:
                logger.debug(f"[Window] Could not bring to front: {e}")
            
            return jsonify({"status": "success", "task_id": result['task_id']})
        else:
            return jsonify({"status": "error", "message": result.get('error', 'Failed to queue download')}), 500
            
    except Exception as e:
        logger.error(f"[Aria2] Add download failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# [IDM CONTROL API] - PAUSE, RESUME, CANCEL
# ============================================================================

@app.route('/api/downloads/control', methods=['POST'])
def api_control_download():
    """
    Receives signals from the UI to modify the state of a download task.
    Supports: 'pause', 'resume', and 'cancel'.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No control data received."}), 400

    task_id = data.get('id')
    action = data.get('action')

    # Basic Validation
    if not task_id or not action:
        return jsonify({"status": "error", "message": "Task ID and Action are required."}), 400

    print(f"[API] Downloader Signal: {action} for task: {task_id}")
    logger.info(f"[Downloader] User signaled {action} for task: {task_id}")

    try:
        # Hand off the command to the aria2 manager
        if action == "pause":
            result = aria2_dl.pause(task_id)
        elif action == "resume":
            result = aria2_dl.resume(task_id)
        elif action == "cancel":
            result = aria2_dl.cancel(task_id)
        else:
            return jsonify({"status": "error", "message": f"Unsupported action: {action}"}), 400
        
        if result.get('success'):
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": result.get('error', 'Action failed')}), 500

    except Exception as e:
        logger.error(f"[Aria2] Control Logic Failure: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/downloads/update_options', methods=['POST'])
def api_update_download_options():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400
        
    task_id = data.get('id')
    options = data.get('options') # Dict of options
    
    if not task_id or not options:
        return jsonify({"status": "error", "message": "ID and Options are required."}), 400
        
    result = aria2_dl.change_options(task_id, options)
    if result.get('success'):
        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "error", "message": result.get('error', 'Update options failed')}), 500

@app.route('/api/config', methods=['GET'])
def api_get_config():
    config = load_config()
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def api_update_config():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received."}), 400
    
    config = load_config()
    config.update(data)
    save_config(config)
    return jsonify({"status": "success"})

# ============================================================================
# [14] LIBRARY SCANNING & DB SYNC
# ============================================================================

def scan_library_task():
    """Unified library synchronization logic. Now triggers covers automatically."""
    with app.app_context():
        global all_games
        if CURRENT_RUNNING_GAME: return 

        config = load_config()
        scanned = scanner.find_all_games(config)
        
        db = get_db_connection()
        try:
            for s in scanned:
                if not s.install_path: s.install_path = "Unknown"
                uid = f"{s.source}|{s.name}"
                db.execute('''
                    INSERT INTO games (id, name, source, launch_id, install_path)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET 
                        install_path = excluded.install_path,
                        launch_id = excluded.launch_id
                ''', (uid, s.name, s.source, str(s.launch_id), s.install_path))
            db.commit()
            logger.info(f"[Scanner] Processed {len(scanned)} entries.")
        except Exception as e:
            logger.error(f"Scanner error: {e}")
        finally:
            db.close()
        
        load_games_from_db()
        socketio.emit('scan_complete')

        # --- AUTO-TRIGGER COVERS ---
        api_key = config.get('steamgriddb_api_key')
        if api_key:
            logger.info("[Scanner] Library ready. Starting cover fetcher...")
            # We run this in the SAME thread now to ensure it happens after the scan
            fetch_missing_covers(api_key)

# app.py

# app.py - Advanced Cover Resolver

def fetch_missing_covers(api_key):
    """
    ULTRA-FAST COVER RESOLVER:
    1. Scans DB for games missing local artwork.
    2. Checks the SSD first to see if the image already exists (Zero-Bandwidth).
    3. If missing from SSD, fetches from SteamGridDB with name cleaning.
    4. Downloads and caches images locally with permanent browser headers.
    """
    headers = {'Authorization': f'Bearer {api_key}'}
    conn = get_db_connection()
    if not conn: return
    
    cursor = conn.cursor()
    # Find games that don't have a local cached path (/api/covers/...)
    cursor.execute("SELECT id, name, source, launch_id FROM games WHERE grid_image_url NOT LIKE '/api/covers/%' OR grid_image_url IS NULL OR grid_image_url = ''")
    games_to_fix = cursor.fetchall()
    
    if not games_to_fix:
        conn.close()
        return

    logger.info(f"[Covers] Checking local storage for {len(games_to_fix)} potential images...")

    for row in games_to_fix:
        game_id, name, source, launch_id = row['id'], row['name'], row['source'], row['launch_id']
        
        # --- PHASE 1: DISK PRE-CHECK (Saves Download Speed) ---
        safe_id = game_id.replace('|', '_').replace(':', '')
        # Check for standard image formats on SSD
        existing_ext = None
        for ext in ['jpg', 'png', 'webp', 'jpeg']:
            if os.path.exists(os.path.join(COVERS_DIR, f"{safe_id}.{ext}")):
                existing_ext = ext
                break
        
        if existing_ext:
            # If found on SSD, simply link it to the DB and move to next game
            local_url = f"/api/covers/{safe_id}.{existing_ext}"
            conn.execute("UPDATE games SET grid_image_url = ? WHERE id = ?", (local_url, game_id))
            conn.commit()
            load_games_from_db() # Sync memory
            socketio.emit('library_updated')
            continue 

        # --- PHASE 2: REMOTE RESOLUTION (Only if missing from disk) ---
        remote_url = None
        try:
            # A. Try Steam Match
            if source == "Steam" and launch_id:
                res = requests.get(f"{STEAMGRIDDB_API_URL}/grids/steam/{launch_id}", headers=headers, timeout=15)
                if res.ok and res.json().get('data'):
                    remote_url = res.json()['data'][0]['url']
            
            # B. Try Smart Name Search (for Epic, EA, and Manual)
            if not remote_url:
                clean_name = name.replace("EA SPORTS ", "").replace("™", "").replace("®", "").split(":")[0].strip()
                search = requests.get(f"{STEAMGRIDDB_API_URL}/search/autocomplete/{clean_name}", headers=headers, timeout=15)
                if search.ok and search.json().get('data'):
                    sgdb_id = search.json()['data'][0]['id']
                    grids = requests.get(f"{STEAMGRIDDB_API_URL}/grids/game/{sgdb_id}?dimensions=600x900", headers=headers, timeout=15)
                    if grids.ok and grids.json().get('data'):
                        remote_url = grids.json()['data'][0]['url']

            # --- PHASE 3: DOWNLOAD & CACHE ---
            if remote_url:
                file_ext = remote_url.split('.')[-1].split('?')[0] or 'jpg'
                local_filename = f"{safe_id}.{file_ext}"
                local_path = os.path.join(COVERS_DIR, local_filename)

                img_data = requests.get(remote_url, timeout=10).content
                with open(local_path, 'wb') as f:
                    f.write(img_data)
                
                # Link local path to DB
                local_api_url = f"/api/covers/{local_filename}"
                conn.execute("UPDATE games SET grid_image_url = ? WHERE id = ?", (local_api_url, game_id))
                conn.commit()
                
                load_games_from_db()
                socketio.emit('library_updated')
                logger.info(f"[Covers] Successfully cached: {name}")
            else:
                # Mark as missing so we don't spam the API on next boot
                conn.execute("UPDATE games SET grid_image_url = 'MISSING' WHERE id = ?", (game_id,))
                conn.commit()

        except Exception as e:
            logger.error(f"[Covers] Error resolving {name}: {e}")
        
        time.sleep(0.5) # Protect API key from rate limits
    
    conn.close()

def load_games_from_db():
    """
    Refreshes the global list in memory from the SQLite database.
    """
    global all_games
    conn = get_db_connection()
    if not conn: return
    
    try:
        rows = conn.execute("SELECT * FROM games").fetchall()
        temp_list = []
        for r in rows:
            # Helper function to safely get values from sqlite3.Row objects
            def safe_get(row, key, default=None):
                try:
                    return row[key]
                except (KeyError, IndexError):
                    return default
            
            # Reconstruct Game dataclass from SQL row
            g = Game(
                name=r['name'], 
                source=r['source'], 
                launch_id=r['launch_id'], 
                install_path=r['install_path']
            )
            g.favorite = bool(r['favorite'])
            g.hidden = bool(r['hidden'])
            g.playtime_seconds = r['playtime_seconds']
            g.last_played = r['last_played']
            g.grid_image_url = r['grid_image_url']
            g.auto_bridge = bool(r['auto_bridge'])
            g.focus_mode = bool(r['focus_mode'])
            g.avg_fps = r['avg_fps']
            g.best_ping = r['best_ping']
            # Update detection fields - use safe_get instead of .get()
            g.installed_version = safe_get(r, 'installed_version', '') or ''
            g.installed_build = safe_get(r, 'installed_build', '') or ''
            g.last_updated = safe_get(r, 'last_updated', 0) or 0
            g.update_status = safe_get(r, 'update_status', 'UNKNOWN') or 'UNKNOWN'
            g.update_source = safe_get(r, 'update_source', '') or ''
            g.last_update_check = safe_get(r, 'last_update_check', 0) or 0
            temp_list.append(g)
            
        all_games = temp_list
        logger.debug(f"[Database] Memory state hydrated: {len(all_games)} objects.")
    finally:
        conn.close()

# ============================================================================
# [15] APPLICATION LIFECYCLE & TRAY
# ============================================================================

def start_ui_server():
    """Launch the Socket.io server on the background thread."""
    socketio.run(app, host='127.0.0.1', port=SERVER_PORT, allow_unsafe_werkzeug=True, use_reloader=False)

def run_tray_icon():
    """Initializes and runs the Windows System Tray icon."""
    try:
        # Load asset path
        if getattr(sys, 'frozen', False):
            asset_base = sys._MEIPASS
        else:
            asset_base = os.path.dirname(os.path.abspath(__file__))
        
        icon_file = os.path.join(asset_base, 'assets', 'app_icon.ico')
        
        if os.path.exists(icon_file):
            img = Image.open(icon_file)
        else:
            img = Image.new('RGB', (64, 64), color=(88, 101, 242))

        def quit_action(icon, item):
            icon.stop()
            os._exit(0)

        menu = pystray.Menu(
            pystray.MenuItem('Open Dashboard', lambda: webbrowser.open(HOST_URL)),
            pystray.MenuItem('Quit Game Hub', quit_action)
        )
        
        icon_service = pystray.Icon("Game Hub", img, "Game Hub Pro", menu)
        icon_service.run()
    except Exception as e:
        logger.error(f"[Tray] Service failed: {e}")

@app.route('/api/settings', methods=['GET', 'POST'])
def api_handle_settings():
    """Manages application configuration and triggers metadata fetching."""
    config = load_config()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        
        # Preserve existing keys if they weren't sent in the payload
        config.update(data)
        save_config(config)
        
        # Reset Extension tracking if path changed
        if 'extension_path' in data:
            global LAST_SEEN_EXT_VERSION
            LAST_SEEN_EXT_VERSION = "0.0"
            logger.info("[Settings] Extension path changed. Resetting status tracking.")
            
        # --- FIX: TRIGGER COVER FETCHING IMMEDIATELY ---
        api_key = config.get('steamgriddb_api_key')
        if api_key and api_key != "":
            logger.info("[Settings] API Key detected. Launching cover fetcher thread...")
            # Run the cover fetcher in a separate thread so the UI doesn't hang
            threading.Thread(target=fetch_missing_covers, args=(api_key,), daemon=True).start()
            
        return jsonify({"status": "success"})
    
    return jsonify(config)

# --- MISSING ROUTE 1: GAME STATUS ---
@app.route('/api/game_status')
def route_api_game_status():
    """Returns the currently active game process if tracking is active."""
    return jsonify(CURRENT_RUNNING_GAME if CURRENT_RUNNING_GAME else {})

# --- MISSING ROUTE 2: DOWNLOADS LIST ---
@app.route('/api/downloads/list')
def route_api_list_downloads():
    """Returns the current queue of aria2 download tasks."""
    return jsonify(aria2_dl.list_tasks())

# --- MISSING ROUTE 3: CHECK FOR UPDATES ---
@app.route('/api/check_for_updates')
def route_api_check_for_updates():
    """Pings the GitHub Repository to check for newer versions of Game Hub."""
    try:
        headers = {'Accept': 'application/vnd.github.v3+json'}
        # GITHUB_REPO was defined as "Ziadnahouli/GameHub" at the top of the file
        res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", headers=headers, timeout=10)
        
        if not res.ok:
            return jsonify({"update_available": False})

        data = res.json()
        latest_v = data.get("tag_name", "").lstrip("v")
        
        # Compare versions
        if version.parse(latest_v) > version.parse(CURRENT_VERSION):
            # Look for the .exe installer in the GitHub Release Assets
            exe_asset = next((a for a in data.get("assets", []) if a["name"].endswith(".exe")), None)
            if exe_asset:
                return jsonify({
                    "update_available": True,
                    "version": latest_v,
                    "url": exe_asset["browser_download_url"]
                })
        
        return jsonify({"update_available": False, "current": CURRENT_VERSION})
    except Exception as e:
        logger.error(f"[Updater] Check failed: {e}")
        return jsonify({"update_available": False})

@app.route('/api/add_game', methods=['POST'])
def api_add_manual_game():
    """
    Adds a manually selected executable to the library.
    Standardizes the ID to 'Other Games|Name' for reliable deletion.
    """
    data = request.get_json(silent=True)
    name = data.get('name')
    path = data.get('path')

    if not name or not path:
        return jsonify({"status": "error", "message": "Game Title and Path are required."}), 400

    try:
        # Standardized ID construction
        # This matches the ID format used by Steam and Epic scanners
        internal_id = f"Other Games|{name}"
        
        conn = get_db_connection()
        # ON CONFLICT ensures we don't crash if the ID already exists
        conn.execute('''
            INSERT INTO games (id, name, source, launch_id, install_path)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET 
                install_path = excluded.install_path,
                launch_id = excluded.launch_id
        ''', (internal_id, name, 'Other Games', path, path))
        conn.commit()
        conn.close()
        
        # Sync the changes to the app's active memory (all_games list)
        load_games_from_db()
        
        logger.info(f"[Library] Manually registered: {name}")
        return jsonify({"status": "success"})
    except Exception as e:
        logger.error(f"[ManualAdd] Failed to save {name}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- GOOD PLACEMENT (At the bottom of the file, before 'if __name__ == "__main__":') ---

@app.route('/api/delete_game', methods=['POST'])
def api_delete_game():
    data = request.get_json(silent=True)
    name = data.get('name')
    source = data.get('source')
    
    try:
        internal_id = f"{source}|{name}"
        conn = get_db_connection()
        conn.execute("DELETE FROM games WHERE id = ?", (internal_id,))
        conn.commit()
        conn.close()
        load_games_from_db() # Sync memory
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# app.py

@app.route('/api/danger/wipe_db', methods=['POST'])
def wipe_db():
    """Nuclear option: Deletes all rows from the games table."""
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM games")
        conn.commit()
        conn.close()
        
        # Clear the memory list in Python as well
        global all_games
        all_games = []
        
        logger.info("[Database] Manual wipe performed by user.")
        return jsonify({"status": "success", "message": "Database cleared."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def resolve_steam_id(user_input, api_key):
    """Converts a Steam Vanity URL to a 64-bit ID if needed."""
    if user_input.isdigit() and len(user_input) == 17:
        return user_input
    
    # Extract username from URL if they pasted a link
    vanity = user_input.strip('/').split('/')[-1]
    try:
        res = requests.get(f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v0001/", 
                           params={'key': api_key, 'vanityurl': vanity}, timeout=5)
        data = res.json()
        if data.get('response', {}).get('success') == 1:
            return data['response']['steamid']
    except: pass
    return None

@app.route('/api/steam/friends')
def get_steam_friends():
    config = load_config()
    api_key = config.get('steam_api_key')
    raw_id = config.get('steam_id')
    
    if not api_key or not raw_id:
        return jsonify({"status": "error", "message": "Keys missing in Settings."})

    # Resolve the ID in case they used a vanity URL
    steam_id = resolve_steam_id(raw_id, api_key)
    if not steam_id:
        return jsonify({"status": "error", "message": "Invalid Steam ID or URL."})

    try:
        # Get Friend List
        fl_res = requests.get(f"{STEAM_API_BASE}/ISteamUser/GetFriendList/v0001/", 
                              params={'key': api_key, 'steamid': steam_id, 'relationship': 'friend'}, timeout=7)
        friends_raw = fl_res.json().get('friendslist', {}).get('friends', [])
        
        if not friends_raw:
            return jsonify({"status": "success", "friends": []})

        ids = ",".join([f['steamid'] for f in friends_raw[:100]])
        
        # Get Summaries
        sum_res = requests.get(f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v0002/", 
                               params={'key': api_key, 'steamids': ids}, timeout=7)
        players = sum_res.json().get('response', {}).get('players', [])
        
        output = []
        for p in players:
            status = "Online" if p['personastate'] > 0 else "Offline"
            if p.get('gameextrainfo'): status = "In-Game"
            output.append({
                'name': p['personaname'],
                'avatar': p['avatarfull'],
                'status': status,
                'game': p.get('gameextrainfo', '')
            })
        
        # Sort: In-Game -> Online -> Offline
        output.sort(key=lambda x: 0 if x['status'] == 'In-Game' else (1 if x['status'] == 'Online' else 2))
        return jsonify({"status": "success", "friends": output})
    except Exception as e:
        return jsonify({"status": "error", "message": "Steam Privacy settings may be blocking this."})

@app.route('/api/extension/update_remote', methods=['POST'])
def update_extension_remote():
    """
    CLOUD UPDATE ENGINE:
    1. Downloads latest extension.zip from GitHub.
    2. Extracts and overwrites the local 'Source' folder.
    3. Persists the new version number to config.json.
    4. Synchronizes the files to the user's browser directory.
    """
    logger.info("[CloudSync] Initializing remote extension update...")

    try:
        # Step 1: Identify local Source and Destination paths
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            
        src_folder = os.path.normpath(os.path.join(base_path, 'GameHub_Extension'))
        
        config = load_config()
        browser_dest = config.get('extension_path')

        if not browser_dest or not os.path.exists(browser_dest):
            return jsonify({
                "status": "error", 
                "message": "Extension Path not set. Please go to Settings first."
            }), 400

        # Step 2: Download the ZIP from GitHub
        logger.info(f"[CloudSync] Fetching ZIP: {EXTENSION_ZIP_URL}")
        r = requests.get(EXTENSION_ZIP_URL, timeout=45)
        r.raise_for_status()

        # Step 3: Extract ZIP to overwrite the local source folder
        # We use io.BytesIO to keep the ZIP in memory (Faster & Cleaner)
        logger.info("[CloudSync] Unzipping new bridge files...")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            # Overwrite the GameHub_Extension folder inside the app directory
            z.extractall(src_folder)

        # Step 4: Parse the NEW version from the downloaded manifest
        new_manifest_path = os.path.join(src_folder, 'manifest.json')
        if os.path.exists(new_manifest_path):
            with open(new_manifest_path, 'r', encoding='utf-8') as f:
                manifest_data = json.load(f)
                new_version = manifest_data.get('version', '3.5')
                
                # Update config so the UI 'Update' button hides on next refresh
                config['extension_internal_version'] = new_version
                save_config(config)
                logger.info(f"[CloudSync] Successfully updated local source to v{new_version}")

        # Step 5: Execute the Local Sync to push files to Chrome/Edge
        # We reuse our robust api_sync_extension() function
        logger.info("[CloudSync] Files updated. Triggering browser directory sync...")
        sync_response = route_api_sync_extension()
        
        return sync_response

    except requests.exceptions.RequestException as net_err:
        logger.error(f"[CloudSync] Network Error: {net_err}")
        return jsonify({"status": "error", "message": "Failed to reach GitHub. Check your internet."}), 503
    except zipfile.BadZipFile:
        logger.error("[CloudSync] The downloaded ZIP file was corrupted.")
        return jsonify({"status": "error", "message": "Corrupted update file received."}), 500
    except Exception as e:
        logger.error(f"[CloudSync] Critical failure: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/covers/<path:filename>')
def serve_custom_cover(filename):
    """Serves cached covers with high-performance browser caching enabled."""
    try:
        response = send_from_directory(COVERS_DIR, filename)
        # 31536000 seconds = 1 Year
        # This stops the app from using any CPU/Network to 're-fetch' local images
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response
    except:
        return jsonify({"status": "error"}), 404

# app.py

@app.route('/api/library/fetch_covers', methods=['POST'])
def trigger_cover_fetch():
    """API endpoint to manually or automatically start the cover downloader."""
    config = load_config()
    api_key = config.get('steamgriddb_api_key')
    
    if api_key:
        # Start the background thread so the app doesn't freeze
        threading.Thread(target=fetch_missing_covers, args=(api_key,), daemon=True).start()
        return jsonify({"status": "success", "message": "Cover sync started."})
    
    return jsonify({"status": "error", "message": "No API Key configured."})

# app.py - Add this at the top with other globals


@app.before_request
def log_request_info():
    # This will print EVERY request hitting the server to the terminal
    if "/api/extension/" in request.path:
        print(f"DEBUG: Incoming Request: {request.method} {request.path}")

# app.py


# 2. THE CRITICAL HEADER: This bypasses Chrome's "Private Network" block


# Ensure your heartbeat route handles 'OPTIONS' (Chrome sends this first)
# app.py

# app.py

@app.route('/api/extension/report_version', methods=['POST', 'OPTIONS'])
def route_report_ext_version():
    global LAST_SEEN_EXT_VERSION, EXTENSION_RELOAD_SIGNAL, VERSION_TO_IGNORE, EXTENSION_RELOAD_TIME
    
    # Private Network Handshake
    res = Flask.make_response(app, "")
    res.headers['Access-Control-Allow-Private-Network'] = 'true'
    if request.method == 'OPTIONS': return res, 204

    data = request.get_json(silent=True)
    if data and 'version' in data:
        incoming_v = str(data['version']).strip()
        
        # Ghost Filter: Ignore the version we just told to reload
        if incoming_v == VERSION_TO_IGNORE:
            return jsonify({"status": "success", "version": "0.0", "command": "RELOAD"})

        command = "NONE"
        if EXTENSION_RELOAD_SIGNAL:
            command = "RELOAD"
            EXTENSION_RELOAD_SIGNAL = False
            VERSION_TO_IGNORE = incoming_v 
            LAST_SEEN_EXT_VERSION = "0.0" 
            print(f"🚀 [BRIDGE] Dispatching RELOAD. Ignoring old v{incoming_v}")
        else:
            if incoming_v != VERSION_TO_IGNORE: 
                VERSION_TO_IGNORE = ""
                # If we received a new version that's not the ignored one, reload is complete
                if EXTENSION_RELOAD_TIME is not None:
                    EXTENSION_RELOAD_TIME = None
                    print(f"✅ [BRIDGE] Reload complete. New version: v{incoming_v}")
            LAST_SEEN_EXT_VERSION = incoming_v

        return jsonify({"status": "success", "version": LAST_SEEN_EXT_VERSION, "command": command})
    return jsonify({"status": "error"}), 400

@app.route('/api/window/focus', methods=['POST'])
def route_window_focus():
    """Brings the Game Hub window to the front."""
    try:
        bring_window_to_front()
        return jsonify({"status": "success"})
    except Exception as e:
        logger.debug(f"[Window] Focus request failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/extension/get_last_version')
def route_get_last_ext_version():
    global EXTENSION_RELOAD_TIME, LAST_SEEN_EXT_VERSION, EXTENSION_CHECK_GRACE_PERIOD, EXTENSION_RELOAD_GRACE_PERIOD
    import time
    
    # If version is still 0.0, check if we're in grace period (app just started)
    # This prevents false "not installed" locks on app startup
    if LAST_SEEN_EXT_VERSION == "0.0":
        # Check if app has been running for less than grace period
        if not hasattr(route_get_last_ext_version, 'app_start_time'):
            route_get_last_ext_version.app_start_time = time.time()
        
        elapsed = time.time() - route_get_last_ext_version.app_start_time
        if elapsed < EXTENSION_CHECK_GRACE_PERIOD:
            # Return a temporary "checking" state instead of "0.0"
            return jsonify({"version": "CHECKING", "grace_period": True})
    
    # Check if we're in a reload grace period (extension is updating)
    # During reload, version might temporarily mismatch, so we give it time
    if EXTENSION_RELOAD_TIME is not None:
        elapsed_since_reload = time.time() - EXTENSION_RELOAD_TIME
        if elapsed_since_reload < EXTENSION_RELOAD_GRACE_PERIOD:
            # Return a special state indicating reload is in progress
            return jsonify({
                "version": LAST_SEEN_EXT_VERSION, 
                "reload_in_progress": True,
                "grace_period": True
            })
        else:
            # Grace period expired, clear the reload time
            EXTENSION_RELOAD_TIME = None
    
    return jsonify({"version": LAST_SEEN_EXT_VERSION})

@app.after_request
def add_security_headers(response):
    # These three lines are the ONLY way to stop Chrome from blocking Localhost
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Private-Network'] = 'true' # THE FIX
    return response

@app.route('/api/extension/trigger_reload', methods=['POST'])
def route_trigger_reload():
    global EXTENSION_RELOAD_SIGNAL, EXTENSION_RELOAD_TIME
    import time
    EXTENSION_RELOAD_SIGNAL = True
    EXTENSION_RELOAD_TIME = time.time()
    return jsonify({"status": "success"})

@app.route('/api/debug/intercept')
def debug_intercept():
    return jsonify({
        "last_seen_url": getattr(app, 'last_url', 'None'),
        "intercept_rules": ["extensions", "size", "manual_context"],
        "blacklist": ["bank", "login", "checkout"],
        "engine_status": "READY"
    })

@app.route('/api/debug/decisions')
def get_debug_decisions():
    return jsonify(intercept_log.logs)

def atomic_extension_sync(src, dest):
    """
    Atomic extension synchronization with structure validation and rollback.
    
    Args:
        src: Source directory (GameHub_Extension folder)
        dest: Destination directory (browser extension path)
    
    Returns:
        tuple: (success: bool, message: str)
    """
    temp_dir = dest + "_new"
    backup_dir = dest + "_old"
    
    try:
        # Cleanup potential leftover failed syncs
        for p in [temp_dir, backup_dir]:
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

        # 1. Stage: Copy to temp directory
        shutil.copytree(src, temp_dir)
        
        # 2. Structure Validation: Check for required files
        required_files = ['manifest.json', 'background.js', 'content.js']
        for req_file in required_files:
            req_path = os.path.join(temp_dir, req_file)
            if not os.path.exists(req_path):
                raise FileNotFoundError(f"Structure validation failed: {req_file} missing in staged folder")
        
        # 3. Critical Swap: Atomic Windows directory replacement
        if os.path.exists(dest):
            # Rename old -> backup (atomic on Windows)
            try:
                os.rename(dest, backup_dir)
            except OSError as e:
                # If rename fails, try removing old first (Windows file lock workaround)
                shutil.rmtree(dest, ignore_errors=True)
        
        # Rename new -> dest (atomic)
        os.rename(temp_dir, dest)
        
        # 4. Cleanup backup
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        
        logger.info(f"[Bridge] Atomic Sync Successful: {src} -> {dest}")
        return True, "Sync completed successfully"
        
    except Exception as e:
        logger.error(f"[Bridge] Atomic sync failed: {e}")
        
        # Rollback: Restore from backup if dest is missing
        if os.path.exists(backup_dir) and not os.path.exists(dest):
            try:
                os.rename(backup_dir, dest)
                return False, f"Sync failed, restored backup: {str(e)}"
            except Exception as rollback_err:
                logger.error(f"Rollback also failed: {rollback_err}")
                return False, f"Sync and rollback failed: {str(e)}"
        
        # Cleanup temp if it still exists
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        return False, str(e)

@app.route('/api/extension/sync', methods=['POST'])
def api_sync_extension():
    config = load_config()
    dest = os.path.normpath(config.get('extension_path', ''))
    src = os.path.normpath(os.path.join(os.path.dirname(__file__), 'GameHub_Extension'))
    
    if not dest or not os.path.exists(dest):
        return jsonify({"status": "error", "message": "Extension path not configured"}), 400
    
    if not os.path.exists(src):
        return jsonify({"status": "error", "message": "Source extension folder not found"}), 400

    success, message = atomic_extension_sync(src, dest)
    
    if success:
        return jsonify({"status": "success", "message": message})
    else:
        return jsonify({"status": "error", "message": message}), 500    

# --- END OF CODE ---
# ============================================================================
# [16] MASTER ENTRY POINT
# =========================================================================

# ============================================================================
# [16] MASTER ENTRY POINT (Standalone Desktop App Build)
# ============================================================================

# Global function to be called from anywhere
def scan_for_running_games():
    """Checks for active game processes and starts tracker if found."""
    if not all_games: return
    
    print("[Engine] Scanning for active game processes (Deep Scan)...")
    ignore = ['steam.exe', 'epicgameslauncher.exe', 'ea.exe', 'origin.exe', 'galaxyclient.exe', 'svchost.exe', 'explorer.exe']
    
    try:
        # Pre-calculate game metadata for speed
        game_meta = []
        for g in all_games:
            if not g.install_path: continue
            
            install_dir = os.path.dirname(g.install_path).lower()
            exe_name = os.path.basename(g.install_path).lower()
            clean_name = g.name.lower().replace(" ", "")
            game_meta.append((g, clean_name, install_dir, exe_name))
        
        if not game_meta: return

        # Scan all processes
        for proc in psutil.process_iter(['name', 'exe', 'pid']):
            try:
                p_name = proc.info['name'].lower()
                p_exe = proc.info['exe'].lower() if proc.info['exe'] else ""
                
                if p_name in ignore: continue
                
                for game, clean_name, install_dir, exe_name in game_meta:
                    match = False
                    
                    # 1. Exact EXE name match
                    if exe_name and p_name == exe_name: 
                        match = True
                    
                    # 2. Path containment (If running process is inside game folder)
                    elif install_dir and len(install_dir) > 5 and install_dir in p_exe: 
                        match = True
                    
                    # 3. Name Match (High confidence only)
                    elif len(clean_name) > 3 and clean_name in p_name.replace(".exe", ""):
                        match = True

                    if match:
                        print(f"[Engine] FOUND MATCH: {game.name} (PID: {proc.pid})")
                        
                        # Start tracker effectively "resuming" the session
                        # Use quick_scan=True to avoid hang
                        global CURRENT_TRACKER
                        if not CURRENT_TRACKER or not getattr(CURRENT_TRACKER, 'is_alive', lambda: False)():
                            try:
                                tracker = PlaytimeTracker(game, quick_scan=True, initial_pid=proc.pid)
                                tracker.start()
                                CURRENT_TRACKER = tracker
                            except NameError:
                                print(f"[Engine] PlaytimeTracker not defined (NameError).")
                            except Exception as e:
                                print(f"[Engine] Failed to start tracker: {e}")
                        return 
                        
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        print(f"[Engine] Auto-detect failed: {e}")

if __name__ == '__main__':
    # --- STEP 1: OPERATING SYSTEM IDENTITY ---
    # This unlinks the process from the Python interpreter so the taskbar
    # shows the Game Hub icon instead of the Python logo.
    try:
        import ctypes
        # Use a unique string to identify this specific app version in Windows
        myappid = 'ziadnahouli.gamehub.pro.v5.0' 
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        print("[System] Taskbar identity established.")
    except Exception as e:
        print(f"[System] Identity Handshake Failed: {e}")

    # --- STEP 2: CORE INFRASTRUCTURE INITIALIZATION ---
    # Prepare the local environment before any UI is drawn.
    try:
        # Initialize SQLite tables
        init_db()
        # Perform any pending schema migrations
        check_and_update_db_schema()
        
        # Start the aria2 manager now that the DB tables exist
        aria2_dl.start()
        
        # Ensure the cover art directory exists for the instant-load feature
        if not os.path.exists(COVERS_DIR):
            os.makedirs(COVERS_DIR)
            print(f"[System] Created local asset cache at: {COVERS_DIR}")
            
        # Pre-load the game library into RAM for instant API response
        load_games_from_db()
    except Exception as e:
        print(f"CRITICAL: System initialization failure: {e}")
        sys.exit(1)

    # --- STEP 3: ASSET PATH RESOLUTION ---
    # We locate the app icon using absolute paths to support both 
    # the raw .py environment and the compiled .exe environment.
    if getattr(sys, 'frozen', False):
        # Path used by PyInstaller EXE
        BASE_PATH = sys._MEIPASS
    else:
        # Path used during development
        BASE_PATH = os.path.dirname(os.path.abspath(__file__))
    
    app_icon = os.path.join(BASE_PATH, 'assets', 'app_icon.ico')



    # --- STEP 4: BACKGROUND ENGINE ORCHESTRATION ---
    def run_backend_engine():
        """
        Starts the internal services in a dedicated background thread.
        This prevents the UI window from freezing during heavy processing.
        """
        print(f"[Engine] Starting Flask/Socket.IO on {HOST_URL}...")
        
        # Check if the library is empty; if so, trigger a scan automatically
        if not all_games:
            print("[Engine] Library empty. Launching auto-scan...")
            threading.Thread(target=scan_library_task, daemon=True).start()
        else:
            scan_for_running_games()

        # Fire up the Socket.IO server
        # use_reloader=False is mandatory for PyWebView stability
        try:
            socketio.run(
                app, 
                host='127.0.0.1', 
                port=SERVER_PORT, 
                allow_unsafe_werkzeug=True, 
                use_reloader=False
            )
        except Exception as e:
            print(f"CRITICAL: Flask Server crashed: {e}")

    # Dispatch the backend thread
    server_thread = threading.Thread(target=run_backend_engine, daemon=True)
    server_thread.start()

    # --- STEP 5: THE WARM-UP LOOP (FIXES 404 ERRORS) ---
    # We ping our own server until it responds. Only then do we open the window.
    print("[Main] Warming up local server...")
    is_warm = False
    for attempt in range(30): # Attempt for 15 seconds
        try:
            # We try to fetch the settings as a health check
            test_res = requests.get(f"{HOST_URL}/api/settings", timeout=1)
            if test_res.ok:
                print(f"[Main] Server warmed up after {attempt * 0.5} seconds.")
                is_warm = True
                break
        except:
            time.sleep(0.5)

    if not is_warm:
        print("CRITICAL: Local backend failed to start. Port 5000 may be blocked.")
        sys.exit(1)

    # --- STEP 6: GUI LIFECYCLE MANAGEMENT ---
    # Create the standalone desktop window
    window = webview.create_window(
        title='Game Hub Pro', 
        url=HOST_URL,
        width=1450, 
        height=900,
        min_size=(1100, 750),
        background_color='#0b0f19', # Matches CSS --bg-dark to prevent white flash
        resizable=True,
        confirm_close=True          # Prevents accidental closing during downloads
    )
    
    # Store window reference globally for focus operations
    GLOBAL_WINDOW = window

    # Launch the Chromium/Edge Webview2 engine
    # gui='cls' ensures a modern Windows Titlebar
    # debug=True allows you to right-click -> Inspect (remove for final release)
    print(f"[Main] UI Server online. Dispatching window...")
    
    webview.start(
        gui='cls', 
        debug=False, 
        icon=app_icon
    )
    
    # --- CLEAN SHUTDOWN ---
    # This code executes ONLY after the window is closed by the user.
    print("[Main] Termination signal received. Closing engine threads.")
    # Stop the ps4 bridge if it's running
    try: ps4_bridge.bridge.stop()
    except: pass
    
    # Use os._exit(0) to bypass standard cleanup and kill all background threads instantly
    os._exit(0)
