import threading
import time
import uuid
import sqlite3
import logging
import os
from datetime import datetime
import aria2_manager

logger = logging.getLogger("Aria2DownloadManager")

class Aria2Task:
    def __init__(self, task_id, gid, url, save_dir, filename="", status="DOWNLOADING", created_ts=None, total_bytes=0, downloaded_bytes=0):
        self.task_id = task_id
        self.gid = gid
        self.url = url
        self.save_dir = save_dir
        self.filename = filename
        self.status = status
        self.created_ts = created_ts or time.time()
        self.updated_ts = time.time()
        self.total_bytes = total_bytes
        self.downloaded_bytes = downloaded_bytes
        self.speed_bps = 0
        self.connections = 0
        self.error_code = ""
        self.error_message = ""

    def to_dict(self):
        # Progress calculation
        progress = 0
        if self.total_bytes > 0:
            progress = (self.downloaded_bytes / self.total_bytes) * 100
        
        # Friendly speed 
        speed_mb = round(self.speed_bps / (1024 * 1024), 2)
        
        return {
            "id": self.task_id,
            "task_id": self.task_id,
            "gid": self.gid,
            "url": self.url,
            "filename": self.filename or "Resolving...",
            "path": self.save_dir,
            "dest_folder": self.save_dir,
            "downloaded": f"{round(self.downloaded_bytes / (1024*1024), 1)} MB",
            "total": f"{round(self.total_bytes / (1024*1024), 1)} MB" if self.total_bytes > 0 else "--- MB",
            "raw_downloaded": self.downloaded_bytes,
            "raw_total": self.total_bytes,
            "speed": f"{speed_mb} MB/s",
            "speed_bps": self.speed_bps,
            "progress": round(progress, 1),
            "status": self._map_status(self.status),
            "error": self.error_message,
            "category": self._determine_category()
        }

    def _map_status(self, status):
        # Map internal status to UI status
        mapping = {
            "DOWNLOADING": "Downloading",
            "PAUSED": "Paused",
            "COMPLETED": "Completed",
            "FAILED": "Failed",
            "CANCELED": "Cancelled",
            "ACTIVE": "Downloading",
            "WAITING": "Queued",
            "REMOVED": "Cancelled"
        }
        return mapping.get(status, status)

    def _determine_category(self):
        if not self.filename or "." not in self.filename: return 'General'
        ext = self.filename.split('.')[-1].lower()
        if ext in ['zip', 'rar', '7z', 'iso']: return 'Compressed'
        if ext in ['exe', 'msi', 'apk']: return 'Programs'
        if ext in ['mp4', 'mkv', 'avi', 'mov']: return 'Video'
        if ext in ['mp3', 'wav', 'flac']: return 'Music'
        return 'General'

class Aria2DownloadManager:
    def __init__(self, db_path, socketio=None):
        self.db_path = db_path
        self.socketio = socketio
        self.tasks = {} # task_id -> Aria2Task
        self.gid_to_id = {} # gid -> task_id
        self.lock = threading.Lock()
        self._running = True
        self.poll_interval = 1.0
        
        # Cleanup queue for removed/completed tasks
        self.cleanup_queue = {} # gid -> removal_time

    def start(self):
        aria2_manager.start_aria2() # Assume no secret for now or handle later
        self.bootstrap_from_aria2()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def bootstrap_from_aria2(self):
        logger.info("Bootstrapping aria2 tasks from DB...")
        conn = self._get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM aria2_tasks")
        rows = cursor.fetchall()
        
        with self.lock:
            for row in rows:
                # Handle optional columns for backward compatibility
                try:
                    total_bytes = row['total_bytes']
                except (KeyError, IndexError):
                    total_bytes = 0
                try:
                    downloaded_bytes = row['downloaded_bytes']
                except (KeyError, IndexError):
                    downloaded_bytes = 0
                    
                task = Aria2Task(
                    task_id=row['task_id'],
                    gid=row['gid'],
                    url=row['url'],
                    save_dir=row['save_dir'],
                    filename=row['filename'],
                    status=row['status'],
                    created_ts=row['created_ts'],
                    total_bytes=total_bytes,
                    downloaded_bytes=downloaded_bytes
                )
                self.tasks[task.task_id] = task
                self.gid_to_id[task.gid] = task.task_id

        # Sync with live aria2
        try:
            active = aria2_manager.rpc_call("aria2.tellActive")
            waiting = aria2_manager.rpc_call("aria2.tellWaiting", [0, 1000])
            stopped = aria2_manager.rpc_call("aria2.tellStopped", [0, 1000])
            all_live = active + waiting + stopped
            
            live_gids = {item['gid'] for item in all_live}
            
            with self.lock:
                for task_id, task in self.tasks.items():
                    if task.gid not in live_gids and task.status in ["DOWNLOADING", "PAUSED"]:
                        task.status = "FAILED"
                        task.error_message = "Task not found in aria2 (possibly external removal)"
        except Exception as e:
            logger.error(f"Bootstrap sync failed: {e}")
        finally:
            conn.close()

    def add(self, url, save_dir, filename=None, options=None):
        task_id = str(uuid.uuid4())
        
        # Base options
        rpc_options = {
            "dir": save_dir,
            "continue": "true",
            "allow-overwrite": "true",
            "auto-file-renaming": "false"
        }
        
        # User-provided filename
        if filename:
            rpc_options["out"] = filename
            
        # Merge advanced options if provided
        # These come from the frontend (e.g., split, max-connection-per-server)
        if options:
            for k, v in options.items():
                if v is not None and v != "":
                    rpc_options[k] = str(v)

        print(f"[Aria2 Manager] Requesting download: {url}")
        print(f"[Aria2 Manager] Options: {rpc_options}")
        try:
            gid = aria2_manager.rpc_call("aria2.addUri", [[url], rpc_options])
            
            task = Aria2Task(task_id, gid, url, save_dir, filename or "")
            with self.lock:
                self.tasks[task_id] = task
                self.gid_to_id[gid] = task_id
            
            self._save_task_to_db(task)
            return {"success": True, "task_id": task_id, "gid": gid}
        except Exception as e:
            logger.error(f"Failed to add download: {e}")
            return {"success": False, "error": str(e)}

    def pause(self, task_id):
        print(f"[Aria2 Manager] Request: PAUSE for {task_id}")
        task = self.tasks.get(task_id)
        if not task: return {"success": False, "error": "Task not found"}
        try:
            aria2_manager.rpc_call("aria2.pause", [task.gid])
            
            # Update and emit immediately for UI responsiveness
            task.status = "PAUSED"
            if self.socketio:
                self.socketio.emit('download_update', task.to_dict())
            self._update_task_db(task)
            
            return {"success": True}
        except Exception as e:
            print(f"[Aria2 Manager] Pause error: {e}")
            return {"success": False, "error": str(e)}

    def resume(self, task_id):
        print(f"[Aria2 Manager] Request: RESUME/RETRY for {task_id}")
        task = self.tasks.get(task_id)
        if not task: return {"success": False, "error": "Task not found"}
        
        try:
            if task.status == 'FAILED':
                print(f"[Aria2 Manager] Task {task_id} is FAILED. Attempting re-add/retry...")
                # 1. Remove old result from aria2
                try:
                    aria2_manager.rpc_call("aria2.removeDownloadResult", [task.gid])
                except: pass
                
                # 2. Re-add same URL/Options
                rpc_options = {
                    "dir": task.save_dir,
                    "continue": "true",
                    "allow-overwrite": "true"
                }
                if task.filename: rpc_options["out"] = task.filename
                
                new_gid = aria2_manager.rpc_call("aria2.addUri", [[task.url], rpc_options])
                
                # 3. Update task object
                old_gid = task.gid
                task.gid = new_gid
                task.status = "DOWNLOADING"
                task.error_message = ""
                
                with self.lock:
                    if old_gid in self.gid_to_id: del self.gid_to_id[old_gid]
                    self.gid_to_id[new_gid] = task.task_id
                
                self._update_task_db(task)
                
                # Emit update immediately
                if self.socketio:
                    self.socketio.emit('download_update', task.to_dict())
                    
                return {"success": True}
            
            # Normal resume (unpause)
            aria2_manager.rpc_call("aria2.unpause", [task.gid])
            
            # Update and emit immediately
            task.status = "DOWNLOADING"
            if self.socketio:
                self.socketio.emit('download_update', task.to_dict())
            self._update_task_db(task)
            
            return {"success": True}
        except Exception as e:
            print(f"[Aria2 Manager] Resume error: {e}")
            return {"success": False, "error": str(e)}

    def cancel(self, task_id):
        print(f"[Aria2 Manager] Request: CANCEL for {task_id}")
        task = self.tasks.get(task_id)
        if not task: return {"success": False, "error": "Task not found"}
        try:
            # Attempt to remove from aria2
            try:
                aria2_manager.rpc_call("aria2.remove", [task.gid])
            except Exception as e:
                # If it's already stopped/failed, aria2.remove might fail, try removeDownloadResult
                try:
                    aria2_manager.rpc_call("aria2.removeDownloadResult", [task.gid])
                except Exception as e2:
                    print(f"[Aria2 Manager] Remove failed in both modes: {e}, {e2}")
            
            task.status = "CANCELED"
            self._update_task_db(task)
            
            # CRITICAL: Emit update BEFORE deleting from memory so UI removes the card
            if self.socketio:
                self.socketio.emit('download_update', task.to_dict())
            
            # Remove from memory so it disappears from UI
            with self.lock:
                if task_id in self.tasks:
                    del self.tasks[task_id]
                if task.gid in self.gid_to_id:
                    del self.gid_to_id[task.gid]
            
            return {"success": True}
        except Exception as e:
            print(f"[Aria2 Manager] Cancel error: {e}")
            return {"success": False, "error": str(e)}

    def list_tasks(self):
        with self.lock:
            return [t.to_dict() for t in self.tasks.values()]

    def change_options(self, task_id, options):
        print(f"[Aria2 Manager] Changing options for {task_id}: {options}")
        task = self.tasks.get(task_id)
        if not task: return {"success": False, "error": "Task not found"}
        
        # aria2.changeOption only works on active/waiting downloads
        if task.status not in ["DOWNLOADING", "WAITING", "PAUSED"]:
            return {"success": False, "error": f"Cannot change options for {task.status} downloads"}
        
        try:
            aria2_manager.rpc_call("aria2.changeOption", [task.gid, options])
            return {"success": True}
        except Exception as e:
            print(f"[Aria2 Manager] ChangeOption error: {e}")
            return {"success": False, "error": str(e)}

    def _poll_loop(self):
        while self._running:
            try:
                active = aria2_manager.rpc_call("aria2.tellActive")
                waiting = aria2_manager.rpc_call("aria2.tellWaiting", [0, 1000])
                stopped = aria2_manager.rpc_call("aria2.tellStopped", [0, 1000])
                all_items = active + waiting + stopped
                
                has_active = len(active) > 0
                
                with self.lock:
                    for item in all_items:
                        gid = item['gid']
                        task_id = self.gid_to_id.get(gid)
                        if not task_id: continue
                        
                        task = self.tasks[task_id]
                        old_status = task.status
                        
                        # Update status
                        aria_status = item['status']
                        if aria_status == 'active': task.status = "DOWNLOADING"
                        elif aria_status == 'waiting': task.status = "WAITING"
                        elif aria_status == 'paused': task.status = "PAUSED"
                        elif aria_status == 'complete': task.status = "COMPLETED"
                        elif aria_status == 'error': task.status = "FAILED"
                        elif aria_status == 'removed': task.status = "CANCELED"
                        
                        # Update progress
                        task.total_bytes = int(item.get('totalLength', 0))
                        task.downloaded_bytes = int(item.get('completedLength', 0))
                        task.speed_bps = int(item.get('downloadSpeed', 0))
                        task.connections = int(item.get('connections', 0))
                        
                        # Update filename if not set
                        if not task.filename and item.get('files'):
                            fpath = item['files'][0].get('path')
                            if fpath:
                                task.filename = os.path.basename(fpath)
                        
                        # Update error info
                        if aria_status == 'error':
                            task.error_code = item.get('errorCode', '')
                            # aria2 sometimes doesn't provide errorMessage, use errorCode if needed
                            task.error_message = item.get('errorMessage', f"Aria2 Error Code: {task.error_code}")
                            print(f"[Aria2 Manager] Task {task.task_id} FAILED: {task.error_message}")

                        if old_status != task.status:
                            task.updated_ts = time.time()
                            self._update_task_db(task)
                        
                        # Socket update
                        if self.socketio:
                            self.socketio.emit('download_update', task.to_dict())
                            
                        # Handle cleanup for memory leak
                        if task.status in ["COMPLETED", "FAILED", "CANCELED"]:
                            if gid not in self.cleanup_queue:
                                self.cleanup_queue[gid] = time.time() + 10 # Cleanup in 10s

                    # Run cleanup
                    now = time.time()
                    for gid, cleanup_time in list(self.cleanup_queue.items()):
                        if now >= cleanup_time:
                            try:
                                aria2_manager.rpc_call("aria2.removeDownloadResult", [gid])
                            except: pass
                            del self.cleanup_queue[gid]

                # Throttling
                self.poll_interval = 1.0 if has_active else 3.0
                time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Poll loop error: {e}")
                time.sleep(5)

    def _save_task_to_db(self, task):
        try:
            conn = self._get_db()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO aria2_tasks (task_id, gid, url, save_dir, filename, status, created_ts, updated_ts, total_bytes, downloaded_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (task.task_id, task.gid, task.url, task.save_dir, task.filename, task.status, task.created_ts, task.updated_ts, task.total_bytes, task.downloaded_bytes))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB save error: {e}")

    def _update_task_db(self, task):
        try:
            conn = self._get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE aria2_tasks SET status = ?, filename = ?, updated_ts = ?, total_bytes = ?, downloaded_bytes = ? WHERE task_id = ?
            """, (task.status, task.filename, task.updated_ts, task.total_bytes, task.downloaded_bytes, task.task_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"DB update error: {e}")
