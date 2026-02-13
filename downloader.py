# downloader.py - v8.0.0 Final Master Build (Reliability Update)
import os
import time

import threading
import requests
import logging
import json
import re
import uuid
import yt_dlp
from queue import Queue, Empty
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- DIRECTORY SETUP ---
DATA_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub')
STATE_FILE = os.path.join(DATA_DIR, 'downloads.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

# --- HIGH-PERFORMANCE NETWORK POOL ---
adapter = HTTPAdapter(pool_connections=25, pool_maxsize=100, max_retries=Retry(total=3))
http_session = requests.Session()
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

logger = logging.getLogger("DownloaderEngine")

# ============================================================================
# [1] TASK STATE MODEL
# ============================================================================

class TaskState:
    QUEUED = "Queued"
    RESOLVING = "Resolving"
    DOWNLOADING = "Downloading"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

class DownloadTask:
    def __init__(self, task_id, url, dest_folder, filename="Resolving...", format_id=None, headers=None):
        self.id = task_id
        self.url = url
        self.dest_folder = dest_folder
        self.filename = unquote(filename) # Decode Arabic/Symbols
        self.format_id = format_id
        self.headers = headers or {}
        
        # Telemetry
        self.status = TaskState.QUEUED
        self.progress = 0.0
        self.speed = 0.0
        self.total_size = 0
        self.downloaded_bytes = 0
        
        # Internal control
        self.stop_signal = threading.Event()
        self.bytes_lock = threading.Lock()
        self.last_sync_time = time.time()
        self.last_bytes_count = 0
        self.error_message = ""
        self.filepath = ""
        self.real_url = None 
        self.accepts_ranges = False # D2: Per-task range support flag 
        
        # Reliability Fields
        self.verified_bytes_written = 0
        self.parts_lock = threading.Lock()
        
        # Doctor Diagnostics
        self.host = ""
        self.http_status_last = 0
        self.stall_count = 0
        self.speed_samples = [] # Ring buffer
        self.diagnostics = {}   # Active flags
        self.applied_rules = {} # Rules used for this run

    def add_verified_bytes(self, count):
        """Thread-safe byte increment for verified writes (Integrity)."""
        with self.parts_lock:
            self.verified_bytes_written += count
            # D5: Decoupled from UI progress for smoothness

    def add_progress(self, count):
        """Thread-safe byte increment for UI progress."""
        with self.bytes_lock:
            self.downloaded_bytes += count

    def reset_speed_metrics(self):
        """Resets speed-related timers to ensure accurate start."""
        with self.bytes_lock:
            self.last_sync_time = time.time()
            self.last_bytes_count = self.downloaded_bytes
            self.speed = 0.0

    def to_dict(self):
        return {
            'id': self.id, 'url': self.url, 'filename': self.filename,
            'status': self.status, 'progress': round(self.progress, 1),
            'speed': f"{self.speed:.2f} MB/s" if self.status == TaskState.DOWNLOADING else "0.00 MB/s",
            'downloaded': f"{self.downloaded_bytes / (1024*1024):.1f} MB",
            'total': f"{self.total_size / (1024*1024):.1f} MB" if self.total_size > 0 else "--- MB",
            'raw_total': self.total_size, 'raw_downloaded': self.downloaded_bytes,
            'category': self._determine_category(), 'path': self.dest_folder,
            'headers': self.headers, 'real_url': self.real_url,
            'error': self.error_message
        }

    def _determine_category(self):
        if not self.filename or "." not in self.filename: return 'General'
        ext = self.filename.split('.')[-1].lower()
        if ext in ['zip', 'rar', '7z', 'iso']: return 'Compressed'
        if ext in ['exe', 'msi', 'apk']: return 'Programs'
        if ext in ['mp4', 'mkv', 'avi', 'mov']: return 'Video'
        if ext in ['mp3', 'wav', 'flac']: return 'Music'
        return 'General'

# ============================================================================
# [2] PROVIDERS
# ============================================================================

class StandardFileProvider:
    def can_handle(self, url): return True

    def resolve(self, task):
        try:
            # HEAD request to check ranges and get name
            with http_session.head(task.url, headers=task.headers, allow_redirects=True, timeout=15) as r:
                task.real_url = r.url
                task.total_size = int(r.headers.get('content-length', 0))
                
                # Check Range Support
                task.accepts_ranges = r.headers.get('Accept-Ranges', '').lower().strip() == 'bytes'
                
                if task.filename == "Resolving...":
                    task.filename = self._extract_name(r)
        except Exception as e:
            # Fallback to GET if HEAD fails
            try:
                with http_session.get(task.url, headers=task.headers, stream=True, timeout=15) as r:
                    task.real_url = r.url
                    task.total_size = int(r.headers.get('content-length', 0))
                    task.accepts_ranges = False # Assume false on fallback
                    if task.filename == "Resolving...":
                        task.filename = self._extract_name(r)
            except Exception as e2:
                logger.error(f"File Resolution Error: {e2}")
                task.status = TaskState.FAILED
                task.error_message = "Resolution Failed"

    def _extract_name(self, r):
        cd = r.headers.get('Content-Disposition', '')
        if 'filename=' in cd:
            res = re.findall('filename="?([^"]+)"?', cd)
            if res: return unquote(res[0])
        name = os.path.basename(urlparse(r.url).path) or f"file_{int(time.time())}"
        return unquote(name)

    def download(self, task, emit_func, doctor_cb=None):
        cat = task._determine_category()
        final_dir = os.path.join(task.dest_folder, cat)
        os.makedirs(final_dir, exist_ok=True)
        task.filepath = os.path.join(final_dir, task.filename)
        
        # D5: Use .part file
        temp_path = task.filepath + ".part"

        # Initialize file
        if not os.path.exists(temp_path):
            with open(temp_path, 'wb') as f:
                pass # Create empty file

        # D1: Range Validation Logic
        if task.accepts_ranges and task.total_size > 0:
            # Dynamic Worker Scaling
            if task.total_size > 100 * 1024 * 1024:    # > 100MB
                workers = 24
            elif task.total_size > 50 * 1024 * 1024:   # > 50MB
                workers = 16
            elif task.total_size > 10 * 1024 * 1024:   # > 10MB
                workers = 8
            else:
                workers = 2
        else:
            workers = 1
        
        if workers > 1:
            # D5: Avoid sparse file preallocation that mimics success
            # Create empty file instead of preallocating
            with open(temp_path, 'wb') as f:
                pass  # Create empty file
            
            part_size = task.total_size // workers
            ranges = [(i * part_size, (i + 1) * part_size - 1 if i < workers - 1 else task.total_size - 1) for i in range(workers)]
            
            # D5: Reset speed timer just before threads start to ignore setup time
            task.reset_speed_metrics()
            
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = []
                for r in ranges:
                    futures.append(executor.submit(self._fetch_range, task, r[0], r[1], temp_path))
                
                while any(f.running() for f in futures):
                    if task.stop_signal.is_set(): break
                    time.sleep(0.5); emit_func(task)
                    
                # Check for exceptions and fallback handling
                range_failed = False
                for f in futures:
                    if f.exception():
                        exc = f.exception()
                        if "does not support ranges" in str(exc) or "HTTP 200" in str(exc):
                            range_failed = True
                            break
                        task.status = TaskState.FAILED
                        task.error_message = str(exc)
                        return
                    elif f.result() is False:  # Range not supported
                        range_failed = True
                        break
                
                # D6: Fallback to single-thread if ranges are not supported
                if range_failed:
                    logger.warning("[Downloader] Range requests failed, switching to single-thread mode")
                    task.verified_bytes_written = 0  # Reset counter
                    self._fetch_single(task, temp_path, emit_func)
        else:
            task.reset_speed_metrics()
            self._fetch_single(task, temp_path, emit_func)
        
        # D3: Completion Verification
        if task.stop_signal.is_set():
             # D7: Pause/Cancel detected, skip corruption check
             return

        if not task.stop_signal.is_set() and task.status != TaskState.FAILED:
            if task.total_size > 0 and task.verified_bytes_written != task.total_size:
                 task.status = TaskState.FAILED
                 task.error_message = f"Corruption: {task.verified_bytes_written}/{task.total_size}"
            else:
                # Atomic Rename
                if os.path.exists(task.filepath): os.remove(task.filepath)
                os.rename(temp_path, task.filepath)
                task.status = TaskState.COMPLETED
                task.progress = 100
                emit_func(task, force=True)

    def _fetch_range(self, task, start, end, temp_path):
        h = task.headers.copy()
        h['Range'] = f'bytes={start}-{end}'
        expected = (end - start) + 1
        
        # Retry Logic with Backoff
        for attempt in range(3):
            if task.stop_signal.is_set(): return
            try:
                with http_session.get(task.real_url, headers=h, stream=True, timeout=20) as r:
                    # D1: Range Validation - Check HTTP 206 status
                    if r.status_code == 200:
                          logger.warning(f"[Downloader] Server returned 200 OK (no range support), falling back to single-thread")
                          return False
                    elif r.status_code != 206:
                         raise Exception(f"Range request failed (HTTP {r.status_code})")
                    
                    with open(temp_path, 'r+b') as f:
                        f.seek(start)
                        chunk_written = 0
                        for chunk in r.iter_content(chunk_size=1024*1024): # 1MB Chunks
                            if task.stop_signal.is_set(): return
                            if chunk:  # Only write non-empty chunks
                                f.write(chunk)
                                l = len(chunk)
                                chunk_written += l
                                task.add_progress(l) # D5: Smooth UI updates
                                
                        # D3: Verify chunk completeness
                        if chunk_written != expected:
                            raise Exception(f"Incomplete chunk: {chunk_written}/{expected} bytes")
                        
                        # D2: Commit verified bytes only after successful range download
                        task.add_verified_bytes(chunk_written)
                        return True  # Success
            except Exception as e:
                # Retry on network errors or non-200/206 codes
                if attempt < 2:
                    backoff_time = 1 + attempt
                    time.sleep(backoff_time)  # Exponential backoff
                else:
                    raise e  # Fail after retries

    def _fetch_single(self, task, temp_path, emit_func):
        """Single-threaded download with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # D4: Resume Logic - Add Range Header if resuming
                h = task.headers.copy()
                resume_pos = 0
                if task.verified_bytes_written > 0 and os.path.exists(temp_path):
                    h['Range'] = f"bytes={task.verified_bytes_written}-"
                    resume_pos = task.verified_bytes_written

                with http_session.get(task.real_url, headers=h, stream=True, timeout=30) as r:
                    if task.total_size == 0: 
                        task.total_size = int(r.headers.get('content-length', 0))
                    
                    # D4: Handling Server Response
                    mode = 'wb'
                    if resume_pos > 0:
                        if r.status_code == 206:
                            mode = 'r+b' # Resume OK
                        elif r.status_code == 200:
                            # Server ignored range, must restart
                            resume_pos = 0
                            task.verified_bytes_written = 0
                            with task.bytes_lock: task.downloaded_bytes = 0 # UI Sync
                        else:
                             raise Exception(f"Server error HTTP {r.status_code}")
                    
                    with open(temp_path, mode) as f:
                        if mode == 'r+b':
                            f.seek(resume_pos)
                        
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if task.stop_signal.is_set(): return
                            if chunk:  # Only write non-empty chunks
                                f.write(chunk)
                                task.add_verified_bytes(len(chunk))
                                task.add_progress(len(chunk)) # D5: Sync UI
                                emit_func(task)
                    return  # Success
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1 + attempt)  # Backoff
                    logger.warning(f"[Downloader] Retry {attempt + 1}/{max_retries}: {str(e)}")
                else:
                    task.status = TaskState.FAILED
                    task.error_message = str(e)

class YTDLPProvider:
    def can_handle(self, url): return any(x in url.lower() for x in ["youtube.com", "youtu.be"])
    def resolve(self, task): pass

    def download(self, task, emit_func):
        def progress_hook(d):
            # Check stop signal in progress hook - but distinguish between pause and cancel
            if task.stop_signal.is_set():
                # If status is PAUSED, we want to stop gracefully
                if task.status == TaskState.PAUSED:
                    raise KeyboardInterrupt("Download paused by user")
                # Otherwise it's a cancellation
                raise KeyboardInterrupt("Download cancelled by user")
            if d['status'] == 'downloading':
                task.downloaded_bytes = d.get('downloaded_bytes', 0)
                task.total_size = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                if task.total_size > 0: task.progress = (task.downloaded_bytes / task.total_size) * 100
                task.speed = d.get('speed', 0) / (1024*1024) if d.get('speed') else 0
                emit_func(task)

        import sys
        base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        ffmpeg_bin = os.path.join(base, "ffmpeg.exe")

        ydl_opts = {
            'format': f'{task.format_id}+bestaudio/best' if task.format_id else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(task.dest_folder, 'Video', '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
            'ffmpeg_location': ffmpeg_bin,
            'quiet': True, 'no_warnings': True,
            'postprocessor_args': {'ffmpeg': ['-c:a', 'aac', '-b:a', '192k']},
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Check stop signal before starting
                if task.stop_signal.is_set():
                    # If status is already PAUSED, keep it paused (don't change to CANCELLED)
                    if task.status != TaskState.PAUSED:
                        task.status = TaskState.CANCELLED
                    emit_func(task, force=True)
                    return
                
                info = ydl.extract_info(task.url, download=True)
                
                # Check stop signal after extraction
                if task.stop_signal.is_set():
                    # If status is already PAUSED, keep it paused (don't change to CANCELLED)
                    if task.status != TaskState.PAUSED:
                        task.status = TaskState.CANCELLED
                        # Only clean up files if it was cancelled, not paused
                        try:
                            temp_file = ydl.prepare_filename(info)
                            if os.path.exists(temp_file):
                                os.remove(temp_file)
                            merged_file = temp_file.rsplit('.', 1)[0] + ".mp4"
                            if os.path.exists(merged_file):
                                os.remove(merged_file)
                        except:
                            pass
                    emit_func(task, force=True)
                    return
                
                task.filename = f"{info['title']}.mp4"
                merged_file = ydl.prepare_filename(info).rsplit('.', 1)[0] + ".mp4"
                task.filepath = merged_file
                if os.path.exists(merged_file):
                    s = os.path.getsize(merged_file)
                    task.total_size = s; task.downloaded_bytes = s
                task.status = TaskState.COMPLETED; task.progress = 100
                emit_func(task, force=True)
        except KeyboardInterrupt as ki:
            # Check if this was a pause or cancel
            if task.status == TaskState.PAUSED:
                # Keep status as PAUSED, don't change it
                # Save current progress
                task.error_message = "Download paused"
                emit_func(task, force=True)
            else:
                # It was a cancellation
                task.status = TaskState.CANCELLED
                task.error_message = "Download cancelled"
                emit_func(task, force=True)
        except Exception as e:
            # Only set as failed if not paused/cancelled
            if task.status == TaskState.PAUSED:
                # Keep paused status
                emit_func(task, force=True)
            elif not task.stop_signal.is_set():
                task.status = TaskState.FAILED
                task.error_message = str(e)
                emit_func(task, force=True)
            else:
                task.status = TaskState.CANCELLED
                emit_func(task, force=True)

# ============================================================================
# [3] MANAGER (SCHEDULER)
# ============================================================================

class DownloadManager:
    def __init__(self):
        self.tasks = {}; self.queue = Queue(); self.semaphore = threading.Semaphore(3)
        self.socketio = None; self._running = True; self.lock = threading.Lock()
        self.providers = [YTDLPProvider(), StandardFileProvider()]
        threading.Thread(target=self._scheduler_loop, daemon=True).start()

    def set_socket(self, s): self.socketio = s
    def quick_enqueue(self, r):
        tid = str(uuid.uuid4()); self.queue.put({"id": tid, "raw": r})
        return {"status": "success", "id": tid}

    def _scheduler_loop(self):
        while self._running:
            try:
                item = self.queue.get(timeout=1)
                if item is None: break
                tid = item["id"]
                if tid in self.tasks and self.tasks[tid].status == TaskState.CANCELLED:
                    self.queue.task_done(); continue
                self.semaphore.acquire()
                threading.Thread(target=self._run_task_cycle, args=(item,), daemon=True).start()
            except Empty: continue

    def _run_task_cycle(self, item):
        tid, raw = item["id"], item["raw"]
        try:
            # Safe config reading (No circular import)
            path = raw.get('path')
            if not path:
                try:
                    with open(CONFIG_FILE, 'r') as f: path = json.load(f).get('default_download_path')
                except: pass
            if not path: path = os.path.join(os.path.expanduser("~"), "Downloads")
            
            if tid in self.tasks: task = self.tasks[tid]
            else:
                task = DownloadTask(tid, raw.get('url'), path, format_id=raw.get('format_id'), headers=raw.get('headers'))
                with self.lock: self.tasks[tid] = task

            provider = next((p for p in self.providers if p.can_handle(task.url)), self.providers[-1])
            if not task.real_url or task.total_size <= 0:
                task.status = TaskState.RESOLVING; self.emit_update(task, force=True); provider.resolve(task)
            
            if task.status != TaskState.FAILED:
                task.status = TaskState.DOWNLOADING; self.emit_update(task, force=True); provider.download(task, self.emit_update)
            
            self.save_state()
        except Exception as e:
            if tid in self.tasks:
                self.tasks[tid].status = TaskState.FAILED; 
                self.tasks[tid].error_message = str(e)
                self.emit_update(self.tasks[tid], force=True)
        finally: self.semaphore.release(); self.queue.task_done()

    def emit_update(self, task, force=False):
        now = time.time(); delta_t = now - task.last_sync_time
        if force or delta_t >= 0.4:
            with task.bytes_lock:
                if task.status == TaskState.COMPLETED: task.progress = 100; task.speed = 0
                elif task.total_size > 0: task.progress = (task.downloaded_bytes / task.total_size) * 100
                else: task.progress = 0
                delta_b = task.downloaded_bytes - task.last_bytes_count
                if delta_b < 0: delta_b = 0 # Guard against resume resets
                if delta_t > 0 and task.status == TaskState.DOWNLOADING:
                    inst = (delta_b / delta_t) / (1024 * 1024)
                    task.speed = (task.speed * 0.7) + (inst * 0.3) if task.speed > 0 else inst
                task.last_sync_time, task.last_bytes_count = now, task.downloaded_bytes
            if self.socketio: self.socketio.emit('download_update', task.to_dict())

    def start_download(self, url, path, headers=None, format_id=None, rule_overrides=None):
        return self.quick_enqueue({'url': url, 'path': path, 'headers': headers, 'format_id': format_id, 'rules': rule_overrides})

    def apply_fix(self, task_id, action_payload):
        """Called by Doctor to execute repair logic."""
        task = self.tasks.get(task_id)
        if not task: return {"status": "error", "message": "Task not found"}
        
        # Pause task first
        self.control_task(task_id, "pause")
        time.sleep(0.5) # Wait for threads
        
        # Prepare overrides
        overrides = getattr(task, 'applied_rules', {}).copy()
        
        if action_payload.get('action') == 'force_single':
            overrides['force_single'] = 1
        elif action_payload.get('action') == 'set_threads':
            overrides['max_threads'] = action_payload.get('threads', 1)
        elif action_payload.get('action') == 'chunk_size':
            overrides['chunk_kb'] = action_payload.get('size_kb', 1024)
            
        # Restart logic
        # For non-resumable fixes (like force_single switching protocol), we might need to reset bytes.
        # But control_task('resume') handles simple restarts.
        # Re-queueing with new rules:
        with self.lock:
            # Update the task definition or just re-queue?
            # Creating a new task entry might be cleaner but we want to keep ID.
            # We'll update the 'raw' rules in the new queue item.
            self.queue.put({
                "id": task_id, 
                "raw": {
                    "url": task.url, 
                    "path": task.dest_folder, 
                    "format_id": task.format_id, 
                    "headers": task.headers,
                    "rules": overrides 
                }
            })
            
        return {"status": "success"}

    def control_task(self, tid, action):
        task = self.tasks.get(tid)
        if not task: return {"status": "error"}
        if action == "pause": 
            task.stop_signal.set()
            task.status = TaskState.PAUSED
        elif action == "resume":
            task.stop_signal.clear()
            task.last_bytes_count = task.downloaded_bytes
            task.last_sync_time = time.time()
            # For YouTube, restart the download (yt-dlp doesn't support true resume)
            # For other downloads, resume from where we left off
            if any(x in task.url.lower() for x in ["youtube.com", "youtu.be"]):
                # YouTube: Reset progress and restart download
                task.downloaded_bytes = 0
                task.verified_bytes_written = 0
                task.progress = 0
                task.status = TaskState.QUEUED
                # Requeue to restart the download
                self.queue.put({"id": tid, "raw": {"url": task.url, "path": task.dest_folder, "format_id": task.format_id, "headers": task.headers}})
            else:
                # Standard downloads: resume from current position
                self.queue.put({"id": tid, "raw": {"url": task.url, "path": task.dest_folder, "format_id": task.format_id, "headers": task.headers}})
        elif action == "cancel":
            task.stop_signal.set()
            task.status = TaskState.CANCELLED
            # For YouTube, clean up partial files
            if any(x in task.url.lower() for x in ["youtube.com", "youtu.be"]) and task.filepath:
                try:
                    if os.path.exists(task.filepath):
                        os.remove(task.filepath)
                except:
                    pass
            with self.lock:
                if tid in self.tasks: del self.tasks[tid]
        self.emit_update(task, force=True)
        self.save_state()
        return {"status": "success"}

    def save_state(self):
        try:
            if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)
            data = [t.to_dict() for t in self.tasks.values()]
            with open(STATE_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
        except: pass

    def load_state(self):
        if not os.path.exists(STATE_FILE): return
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                for d in json.load(f):
                    t = DownloadTask(d['id'], d['url'], d['path'], d['filename'], headers=d.get('headers'))
                    t.downloaded_bytes, t.total_size, t.real_url = d.get('raw_downloaded', 0), d.get('raw_total', 0), d.get('real_url')
                    t.status = TaskState.PAUSED if d['status'] in [TaskState.DOWNLOADING, TaskState.RESOLVING, TaskState.QUEUED] else d['status']
                    if t.total_size > 0: t.progress = (t.downloaded_bytes / t.total_size) * 100
                    self.tasks[t.id] = t
        except: pass

manager = DownloadManager()