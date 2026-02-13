import sys
import socket
import threading
import time
import psutil
import json
import logging
import subprocess
import os
import csv
import uuid
import ctypes
from queue import Queue
from datetime import datetime

logger = logging.getLogger("Optimizer")

class Optimizer:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(Optimizer, cls).__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.active_benchmark = None  # {unique_id, thread, stop_event, data}
        self.last_benchmark_result = None
        self.benchmark_lock = threading.Lock()
        
        # Configuration
        self.SAFE_APPS = ["discord.exe", "chrome.exe", "msedge.exe", "spotify.exe", "steamwebhelper.exe"]
        self.PRESENTMON_PATH = os.path.join(os.getcwd(), "bin", "presentmon.exe")
        # Check for case sensitivity (User has it as Presentmon.exe)
        if not os.path.exists(self.PRESENTMON_PATH):
            alt_path = os.path.join(os.getcwd(), "bin", "Presentmon.exe")
            if os.path.exists(alt_path):
                self.PRESENTMON_PATH = alt_path
        self.OVERLAY_SCRIPT = os.path.join(os.getcwd(), "fps_overlay.py")
        self.socketio = None
        self.overlay_process = None
        self.overlay_active = False
        self.current_pid = None # To be updated from app.py
        self.passive_pm_process = None

    def toggle_overlay(self, enable):
        """Starts or stops the standalone FPS overlay process."""
        if enable:
            if self.overlay_process and self.overlay_process.poll() is None:
                return # Already running
            
            try:
                # Launch without console window
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                # Search and kill any existing overlay instances that might be orphans
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    try:
                        cmd = proc.info.get('cmdline')
                        if cmd and self.OVERLAY_SCRIPT in " ".join(cmd):
                            if proc.pid != os.getpid():
                                proc.terminate()
                                logger.info(f"Terminated orphan overlay: {proc.pid}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied): pass

                self.overlay_process = subprocess.Popen(
                    [sys.executable, self.OVERLAY_SCRIPT],
                    cwd=os.getcwd(),
                    startupinfo=startupinfo
                )
                self.overlay_active = True
                
                # Start streaming thread
                threading.Thread(target=self._stream_overlay_data, daemon=True).start()
                return {"status": "success", "message": "Overlay started"}
            except Exception as e:
                logger.error(f"Failed to start overlay: {e}")
                return {"status": "error", "message": str(e)}
        else:
            self.overlay_active = False
            if self.overlay_process:
                self.overlay_process.terminate()
                self.overlay_process = None
            
            if self.passive_pm_process:
                try: self.passive_pm_process.terminate()
                except: pass
                self.passive_pm_process = None
                
            return {"status": "success", "message": "Overlay stopped"}

    def _stream_overlay_data(self):
        """Streams FPS data to the overlay via UDP."""
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target = ('127.0.0.1', 8899)
        
        # Check if presentmon exists once
        has_tool = os.path.exists(self.PRESENTMON_PATH)
        
        while self.overlay_active and self.overlay_process and self.overlay_process.poll() is None:
            fps = "--"
            
            if not has_tool:
                fps = "ERR: NO TOOL"
            elif self.active_benchmark and 'fps_current' in self.active_benchmark['data']:
                val = self.active_benchmark['data']['fps_current']
                if val: fps = f"{int(val)}"
            elif self.current_pid and psutil.pid_exists(self.current_pid):
                # No active benchmark, but game is running. Start passive pm if needed.
                fps = self._handle_passive_fps()
            else:
                # DEBUG: Why are we here?
                if not hasattr(self, '_pid_warn_ts') or time.time() - self._pid_warn_ts > 10:
                    logger.info(f"[FPS Debug] Overlay active but no valid PID. current_pid={self.current_pid}")
                    self._pid_warn_ts = time.time()
                
                if self.current_pid and not psutil.pid_exists(self.current_pid):
                     logger.debug(f"[FPS Debug] PID {self.current_pid} no longer exists")
            
            try:
                # DEBUG: Log what we are sending
                # logger.debug(f"[FPS Debug] Sending to overlay: {fps}")
                udp.sendto(fps.encode('utf-8'), target)
            except Exception as e:
                logger.error(f"[FPS Debug] UDP Send Failed: {e}")
            
            time.sleep(0.5)

    def is_admin(self):
        """Checks if the current process has administrative privileges."""
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            return False

    def _handle_passive_fps(self):
        """Manages a background PresentMon for the overlay only."""
        # Use absolute path to ensure CSV is found across UAC elevation contexts
        csv_path = os.path.join(os.getcwd(), f"pm_passive_{self.current_pid}.csv")
        
        # 1. Start process if not running
        if not self.passive_pm_process or self.passive_pm_process.poll() is not None:
             try:
                 # Cleanup old CSV if it exists
                 if os.path.exists(csv_path):
                     try: os.remove(csv_path)
                     except: pass
                     
                 # --no_console_stats: Hides the text stream in the console
                 # --restart_as_admin: Requests elevation
                 # --v1_metrics: Simpler CSV format
                 cmd = [
                     self.PRESENTMON_PATH, 
                     "--process_id", str(self.current_pid), 
                     "--output_file", csv_path, 
                     "--stop_existing_session", 
                     "--restart_as_admin", 
                     "--v1_metrics",
                     "--no_console_stats"
                 ]
                 logger.info(f"[FPS Debug] Launching: {' '.join(cmd)}")
                 
                 # CREATE_NO_WINDOW (0x08000000) helps hide the initial shell
                 proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
                 self.passive_pm_process = proc
                 
                 # Wait a moment to see if it survives or restarts
                 time.sleep(1.0)
                 poll_code = proc.poll()
                 if poll_code is not None:
                     stdout, stderr = proc.communicate()
                     logger.error(f"[FPS Debug] PresentMon exited with code {poll_code}")
                     if stderr: logger.error(f"[FPS Debug] Stderr: {stderr.strip()}")
                     if stdout: logger.info(f"[FPS Debug] Stdout: {stdout.strip()}")
                 else:
                     logger.info(f"[FPS Debug] PresentMon running (PID: {proc.pid})")
                     
             except Exception as e:
                 logger.error(f"[FPS Debug] Failed to launch PresentMon: {e}")
                 return "ERR"

        # 2. Read latest FPS
        fps = self._read_latest_fps(csv_path)
        if fps is None:
            # logger.debug(f"[FPS Debug] Passive read returned None for {csv_path}")
            return "--"
        return f"{int(fps)}"

    def _read_latest_fps(self, csv_path):
        """Reads the latest FPS from a PresentMon CSV."""
        if not os.path.exists(csv_path):
            # Only log every 10 samples to avoid spam
            if not hasattr(self, '_pm_missing_warn') or time.time() - self._pm_missing_warn > 5:
                logger.warning(f"[FPS Debug] CSV missing: {csv_path}")
                self._pm_missing_warn = time.time()
            return None
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if len(lines) < 1: return None
                
                # Get last non-empty line
                last_line = None
                for line in reversed(lines):
                    if line.strip():
                        last_line = line
                        break
                
                if not last_line: return None
                
                parts = last_line.split(',')
                # With --v1_metrics, MsBetweenPresents is reliably at index 11 or similar
                # depending on the exact version, but let's just find the first float between 0.5 and 100
                for part in reversed(parts):
                    try:
                        val = float(part.strip())
                        # Look for frame time (usually 0.5ms to 100ms)
                        if 0.5 <= val <= 100.0:
                            return 1000.0 / val
                    except: continue
                
                # Check if the value is actually FPS (sometimes PM outputs FPS directly in special cols)
                for part in reversed(parts):
                    try:
                        val = float(part.strip())
                        if 10 < val < 1000: # High number, likely FPS
                            return val
                    except: continue
        except Exception as e:
            logger.error(f"[FPS Debug] Error reading CSV: {e}")
        return None

    def set_socketio(self, socketio):
        self.socketio = socketio

    def get_status(self, unique_id=None):
        """Returns the strict status payload."""
        # If active, return active data
        if self.active_benchmark:
            # If unique_id requested matches active, return it
            if not unique_id or self.active_benchmark['unique_id'] == unique_id:
                return self.active_benchmark['data']
        
        # If not active, but we have a result for this ID, return it (e.g. DONE state)
        if unique_id and self.last_benchmark_result and self.last_benchmark_result['unique_id'] == unique_id:
            return self.last_benchmark_result

        # Otherwise IDLE
        return {
            "unique_id": None,
            "state": "IDLE",
            "seconds_total": 0,
            "seconds_elapsed": 0,
            "seconds_left": 0,
            "samples_collected": 0,
            "fps_current": None,
            "fps_avg": None,
            "fps_1_low": None,
            "cpu_current": None,
            "cpu_avg": None,
            "gpu_current": None,
            "gpu_avg": None,
            "ram_current": None,
            "ram_avg": None,
            "bottleneck": "None",
            "error": None,
            "recommendations": []
        }

    def start_benchmark(self, unique_id, pid, exe_path, target_mode="Balanced", duration=60):
        with self.benchmark_lock:
            if self.active_benchmark and self.active_benchmark['state'] == 'RUNNING':
                return {"status": "error", "message": "Benchmark already running"}

            # Validate PID
            if not psutil.pid_exists(pid):
                return {"status": "error", "message": "Game process not found"}

            stop_event = threading.Event()
            
            # Initial Data Structure
            data = {
                "unique_id": unique_id,
                "state": "RUNNING",
                "seconds_total": duration,
                "seconds_elapsed": 0,
                "seconds_left": duration,
                "samples_collected": 0,
                "fps_current": None,
                "fps_avg": 0,
                "fps_1_low": 0,
                "cpu_current": 0,
                "cpu_avg": 0,
                "gpu_current": None,
                "gpu_avg": None,
                "ram_current": 0,
                "ram_avg": 0,
                "bottleneck": "Analyzing...",
                "error": None,
                "recommendations": [],
                "history": {
                    "cpu": [], "ram": [], "gpu": [], "fps": []
                }
            }

            self.active_benchmark = {
                "unique_id": unique_id,
                "pid": pid,
                "exe_path": exe_path,
                "target_mode": target_mode,
                "stop_event": stop_event,
                "data": data,
                "state": "RUNNING"
            }

            thread = threading.Thread(target=self._monitor_loop, args=(unique_id, pid, duration))
            thread.daemon = True
            thread.start()
            
            return {"status": "success"}

    def stop_benchmark(self, unique_id):
        with self.benchmark_lock:
            if not self.active_benchmark or self.active_benchmark['unique_id'] != unique_id:
                return {"status": "error", "message": "No active benchmark for this ID"}
            
            self.active_benchmark['stop_event'].set()
            # The thread will update state to DONE
            return {"status": "success"}

    def _monitor_loop(self, unique_id, pid, duration):
        logger.info(f"Starting benchmark for {unique_id} (PID: {pid})")
        
        # Start PresentMon if available
        pm_process = None
        has_presentmon = False
        pm_csv = f"pm_{pid}.csv"
        
        if os.path.exists(self.PRESENTMON_PATH):
            try:
                # Run PresentMon: --process_id PID --output_file pm_PID.csv --stop_existing
                cmd = [self.PRESENTMON_PATH, "--process_id", str(pid), "--output_file", pm_csv, "--stop_existing_session", "--no_csv_header", "--restart_as_admin", "--v1_metrics"]
                pm_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                has_presentmon = True
            except Exception as e:
                logger.error(f"Failed to start PresentMon: {e}")

        try:
            process = psutil.Process(pid)
            
            # Warmup CPU counter (first call is always 0.0 or random)
            process.cpu_percent(None)
            time.sleep(0.2)
            
            start_time = time.time()
            
            for i in range(duration):
                if self.active_benchmark['stop_event'].is_set():
                    break
                
                # check if game is still running
                if not process.is_running():
                    self.active_benchmark['data']['error'] = "Game Closed"
                    self.active_benchmark['stop_event'].set() # Clean exit
                    break

                # 1. Collect Metrics
                try:
                    # Normalize CPU usage to 0-100% of total system capacity
                    cpu_raw = process.cpu_percent(interval=None)
                    cpu = cpu_raw / psutil.cpu_count()
                    ram = process.memory_info().rss / (1024 * 1024) # MB
                    total_ram = psutil.virtual_memory().percent
                    
                    # GPU (Placeholder - hard on Windows without NVML/ADL)
                    gpu = None 
                    
                    # FPS (Read from CSV)
                    fps = None
                    if has_presentmon:
                        fps = self._read_latest_fps(pm_csv)

                    # Update Data
                    data = self.active_benchmark['data']
                    data['seconds_elapsed'] = i + 1
                    data['seconds_left'] = duration - (i + 1)
                    data['samples_collected'] += 1
                    
                    data['cpu_current'] = cpu
                    data['ram_current'] = total_ram # System RAM usage is more relevant for bottleneck
                    data['gpu_current'] = gpu
                    data['fps_current'] = fps
                    
                    data['history']['cpu'].append(cpu)
                    data['history']['ram'].append(total_ram)
                    if fps: data['history']['fps'].append(fps)
                    
                    # Recalculate Averages
                    data['cpu_avg'] = sum(data['history']['cpu']) / len(data['history']['cpu'])
                    data['ram_avg'] = sum(data['history']['ram']) / len(data['history']['ram'])
                    if data['history']['fps']:
                        data['fps_avg'] = sum(data['history']['fps']) / len(data['history']['fps'])
                        # 1% Low (Simple approximation)
                        sorted_fps = sorted(data['history']['fps'])
                        low_idx = max(0, int(len(sorted_fps) * 0.01))
                        data['fps_1_low'] = sorted_fps[low_idx]

                    # Bottleneck Detection
                    if data['gpu_avg'] and data['gpu_avg'] > 95:
                        data['bottleneck'] = "GPU Limited"
                    elif data['cpu_avg'] > 90:
                        data['bottleneck'] = "CPU Limited"
                    elif data['ram_avg'] > 90:
                        data['bottleneck'] = "RAM Limited"
                    else:
                        data['bottleneck'] = "Balanced / Mixed"

                except Exception as e:
                    logger.error(f"Metric collection error: {e}")
                
                # Emit Socket Update
                if hasattr(self, 'socketio') and self.socketio:
                     try:
                        self.socketio.emit('optimizer_update', self.active_benchmark['data'])
                     except: pass

                time.sleep(1.0) # 1s poll interval

            # End of Loop
            self.active_benchmark['data']['state'] = "DONE"
            self.active_benchmark['data']['recommendations'] = self._generate_recommendations(unique_id)
            
            # Emit Final Socket Update
            if hasattr(self, 'socketio') and self.socketio:
                 self.socketio.emit('optimizer_update', self.active_benchmark['data'])
            
            # Allow UI to catch the DONE state briefly if it's polling
            time.sleep(1.0) 
            
            # Move to last_result and clear active to allow new benchmarks
            with self.benchmark_lock:
                self.last_benchmark_result = self.active_benchmark['data']
                self.active_benchmark = None

        except Exception as e:
            logger.error(f"Benchmark failed: {e}")
            if self.active_benchmark:
                self.active_benchmark['data']['state'] = "FAILED"
                self.active_benchmark['data']['error'] = str(e)
                # For failed, we also move to last_result so user sees error
                self.last_benchmark_result = self.active_benchmark['data']
                self.active_benchmark = None

        finally:
            if pm_process:
                pm_process.terminate()
            
            # Cleanup CSV (Retry briefly to avoid lock issues)
            for _ in range(5):
                 if not os.path.exists(pm_csv): break
                 try: 
                    os.remove(pm_csv)
                    break
                 except: 
                    time.sleep(0.5)

    def _read_latest_fps(self, csv_path):
        """Robustly reads the last FPS value from PresentMon CSV."""
        try:
            if not os.path.exists(csv_path): return None
            
            # Read only the last few bytes if file is huge? 
            # For now, standard readlines is fine for 60s files.
            with open(csv_path, 'r') as f:
                lines = f.readlines()
                if len(lines) < 2: return None # Header only or empty
                
                header = lines[0].strip().split(',')
                last_line = lines[-1].strip().split(',')
                
                # Dynamic column detection
                col_idx = -1
                candidates = ["msSinceLastPresent", "msBetweenPresents", "FrameTime"]
                
                for c in candidates:
                    if c in header:
                        col_idx = header.index(c)
                        break
                        
                if col_idx == -1:
                    # Fallback to absolute position (usually 11 or 10)
                    if len(last_line) > 11: col_idx = 11
                    else: return None

                if len(last_line) > col_idx:
                    ms = float(last_line[col_idx])
                    if ms > 0.001: return 1000.0 / ms
        except:
             return None
        return None

        return None

    def _generate_recommendations(self, unique_id):
        # deterministic based on averages
        # Using active_benchmark['data'] before it is cleared
        if not self.active_benchmark: return []
        
        d = self.active_benchmark['data']
        mode = self.active_benchmark['target_mode']
        recs = []

        # 1. Power Plan 
        recs.append({
            "id": "power_high",
            "title": "Enable High Performance Power Plan",
            "reason": "Ensures CPU runs at max frequency to reduce frame time variance.",
            "risk": "Low",
            "requires_admin": True,
            "confidence": 92,
            "expected_gain": "+5-10% CPU Perf",
            "apply_payload": {"type": "power", "value": "high_perf"}
        })

        # 2. Priority
        if d['bottleneck'] == "CPU Limited" or mode == "Max FPS":
             recs.append({
                "id": "priority_high",
                "title": "Set Process Priority to High",
                "reason": "Reduces CPU contention from background apps.",
                "risk": "Low",
                "requires_admin": True,
                "confidence": 85,
                "expected_gain": "+3-8 FPS",
                "apply_payload": {"type": "priority", "value": "HIGH_PRIORITY_CLASS"}
            })

        # 3. Background Apps
        if d['ram_avg'] > 80 or d['bottleneck'] == "CPU Limited":
             recs.append({
                "id": "close_apps",
                "title": "Close Background Apps",
                "reason": "Frees up RAM and CPU cycles for the game.",
                "risk": "Medium",
                "requires_admin": False,
                "confidence": 78,
                "expected_gain": "Free ~1GB RAM",
                "apply_payload": {"type": "close_apps", "target": "safe_list"}
            })

        return recs

    def apply_optimizations(self, unique_id, actions, dry_run=False):
        """
        Applies a list of optimizations and returns the diff.
        """
        diffs = {
            "power_plan": None,
            "registry": [],
            "process": [],
            "closed_apps": []
        }

        if dry_run:
            return {"status": "success", "dry_run": True, "diffs": diffs}

        try:
            # 1. Get PID if known (from last result or active)
            pid = None
            if self.active_benchmark and self.active_benchmark['unique_id'] == unique_id:
                 pid = self.active_benchmark['pid']
            elif self.last_benchmark_result and self.last_benchmark_result['unique_id'] == unique_id:
                 # We need to find PID again if it was closed? 
                 # Actually strictly we should have stored PID context. 
                 # For now, let's assume we can't apply PID changes if game closed.
                 pass
            
            for action in actions:
                atype = action.get('type')
                
                # --- POWER PLAN ---
                if atype == 'power':
                    # Save current plan first
                    # Check current: powercfg /getactivescheme
                    # For MVP, we just apply High Perf (GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c)
                    # And store "Balanced" as rollback.
                    high_perf = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
                    try:
                        subprocess.run(["powercfg", "/setactive", high_perf], check=True)
                        diffs["power_plan"] = "381b4222-f694-41f0-9685-ff5bb260df2e" # Default Balanced GUID
                    except Exception as e:
                        logger.error(f"Power plan failed: {e}")

                # --- PROCESS PRIORITY ---
                elif atype == 'priority' and pid:
                    try:
                        p = psutil.Process(pid)
                        old_nice = p.nice() # usually 'NORMAL_PRIORITY_CLASS' (32)
                        p.nice(psutil.HIGH_PRIORITY_CLASS)
                        diffs["process"].append({"pid": pid, "old_nice": old_nice})
                    except Exception as e:
                        logger.error(f"Priority failed: {e}")

                # --- CLOSE APPS ---
                elif atype == 'close_apps':
                    for proc in psutil.process_iter(['pid', 'name']):
                        try:
                            if proc.info['name'].lower() in ["chrome.exe", "msedge.exe", "discord.exe", "spotify.exe"]:
                                # Don't kill self or game
                                if proc.pid == pid or proc.pid == os.getpid(): continue
                                
                                proc.terminate()
                                diffs["closed_apps"].append({"name": proc.info['name'], "pid": proc.pid})
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue

            return {"status": "success", "diffs": diffs}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def restore_state(self, diffs):
        """Restores system state from diffs."""
        try:
            # 1. Apps (Cannot re-open, just log)
            
            # 2. Process Priority
            if "process" in diffs:
                for p_rec in diffs["process"]:
                    try:
                        p = psutil.Process(p_rec['pid'])
                        p.nice(p_rec['old_nice'])
                    except: pass # Process likely gone

            # 3. Power Plan
            if diffs.get("power_plan"):
                try:
                    subprocess.run(["powercfg", "/setactive", diffs["power_plan"]], check=True)
                except: pass

            return {"status": "success"}
        except Exception as e:
             return {"status": "error", "message": str(e)}

optimizer_engine = Optimizer()
