# game_scanner.py
import os
import winreg
import json
import vdf
import logging
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from game import Game

class GameScanner:
    def find_all_games(self, config: Dict) -> List[Game]:
        games = []
        logging.info("--- STARTING GAME SCAN ---")
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_steam = executor.submit(self._find_steam_games, config)
            future_epic = executor.submit(self._find_epic_games)
            future_ea = executor.submit(self._find_ea_games)
            future_manual = executor.submit(self._load_manual_games)
            
            try: games.extend(future_steam.result())
            except Exception as e: logging.error(f"Steam scan error: {e}")
            
            try: games.extend(future_epic.result())
            except Exception as e: logging.error(f"Epic scan error: {e}")
            
            try: games.extend(future_ea.result())
            except Exception as e: logging.error(f"EA scan error: {e}")
            
            try: games.extend(future_manual.result())
            except Exception as e: logging.error(f"Manual scan error: {e}")
        
        # REMOVE DUPLICATES BEFORE RETURNING
        unique_games = self._deduplicate_games(games)
        
        logging.info(f"--- SCAN COMPLETE. Found {len(unique_games)} games. ---")
        return unique_games

    def _clean_name(self, name):
        if not name: return ""
        # Remove special chars and spaces for comparison
        return name.lower().replace("™", "").replace("®", "").replace(":", "").replace("-", "").replace(" ", "").strip()

    def _deduplicate_games(self, games: List[Game]) -> List[Game]:
        """
        Removes duplicates based on normalized names.
        Prioritizes Official Sources (Steam/Epic) over Others.
        """
        seen = {}
        
        for g in games:
            clean_name = self._clean_name(g.name)
            
            # If we haven't seen this game, add it
            if clean_name not in seen:
                seen[clean_name] = g
            else:
                # If we HAVE seen it, check which one is better
                existing = seen[clean_name]
                
                # Priority: Steam > Epic > EA > Other
                # If existing is 'Other' and new is 'Epic', replace existing
                if existing.source == 'Other Games' and g.source != 'Other Games':
                    seen[clean_name] = g
                # If existing is 'EA' and new is 'Steam' (e.g. bought EA game on Steam), take Steam
                elif existing.source == 'EA' and g.source == 'Steam':
                    seen[clean_name] = g

        return list(seen.values())

    def _is_valid_game_folder(self, folder_path):
        if not folder_path or not os.path.exists(folder_path): return False
        threshold = 1 * 1024 * 1024 # 1 MB
        try:
            for root, dirs, files in os.walk(folder_path):
                if "__installer" in root.lower() or "redist" in root.lower(): continue 
                for f in files:
                    if os.path.getsize(os.path.join(root, f)) > threshold: return True
        except: pass
        return False

    def _has_start_menu_shortcut(self, game_name):
        paths = [
            os.path.join(os.getenv('ProgramData'), r'Microsoft\Windows\Start Menu\Programs'),
            os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs')
        ]
        target = self._clean_name(game_name)
        for path in paths:
            if not os.path.exists(path): continue
            for root, dirs, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        if target in self._clean_name(f.replace(".lnk", "")): return True
        return False

    # --- STEAM ---
    # --- UPDATED STEAM SCANNER ---
    def _find_steam_games(self, config: Dict) -> List[Game]:
        games = []
        library_paths = set()
        
        # 1. Detect default Steam path via Registry
        try:
            hkey = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")
            steam_path = winreg.QueryValueEx(hkey, "InstallPath")[0]
            winreg.CloseKey(hkey)
            library_paths.add(steam_path)
            
            # 2. Parse libraryfolders.vdf for external drives
            vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
            if os.path.exists(vdf_path):
                with open(vdf_path, 'r', encoding='utf-8', errors='ignore') as f:
                    data = vdf.load(f)
                    # Handle different VDF versions
                    for k, v in data.get('libraryfolders', {}).items():
                        if isinstance(v, dict):
                            # New format: "path" key / Old format: key is path
                            path = v.get('path') or v
                            if isinstance(path, str) and os.path.exists(path):
                                library_paths.add(path)
        except Exception as e:
            logging.error(f"Steam Registry/VDF Error: {e}")

        # 3. Add User Config Paths
        for path in config.get('scan_paths', []):
            if os.path.isdir(path):
                library_paths.add(path)

        # 4. Scan all collected library paths
        for lib in library_paths:
            # Steam games live in /steamapps/
            steamapps = os.path.join(lib, "steamapps")
            if not os.path.exists(steamapps): continue
            
            for f in os.listdir(steamapps):
                if f.startswith("appmanifest_") and f.endswith(".acf"):
                    try:
                        with open(os.path.join(steamapps, f), 'r', encoding='utf-8', errors='ignore') as file:
                            m = vdf.load(file).get('AppState', {})
                            name = m.get('name', 'Unknown')
                            appid = m.get('appid')
                            install_dir = m.get('installdir')
                            
                            if name and appid and install_dir:
                                # Construct the REAL path
                                full_path = os.path.join(steamapps, "common", install_dir)
                                
                                # Only add if folder physically exists
                                if os.path.isdir(full_path):
                                    games.append(Game(name, 'Steam', appid, full_path))
                                else:
                                    # Fallback: Add with "Unknown" only if file missing
                                    # But since we want to fix playtime, we prefer finding the path.
                                    # If missing, it might be uninstalled, so we skip or mark Unknown.
                                    games.append(Game(name, 'Steam', appid, steamapps))
                    except: pass
        return games
    
    # --- EPIC ---
    def _find_epic_games(self) -> List[Game]:
        games = []
        try:
            manifests_path = os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'Epic', 'EpicGamesLauncher', 'Data', 'Manifests')
            if os.path.isdir(manifests_path):
                for f in os.listdir(manifests_path):
                    if f.endswith(".item"):
                        try:
                            with open(os.path.join(manifests_path, f), 'r', encoding='utf-8') as file:
                                d = json.load(file)
                                name = d.get('DisplayName')
                                path = d.get('InstallLocation')
                                if self._is_valid_game_folder(path):
                                    games.append(Game(name, 'Epic Games', d.get('AppName'), path))
                        except: pass
        except: pass
        return games
    
    # --- EA GAMES (Improved for FC 26) ---
    def _find_ea_games(self) -> List[Game]:
        games = []
        found_paths = set()

        # Method 1: Registry Scan
        uninstall_map = {}
        for root in [r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"]:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
                for i in range(1024):
                    try:
                        skey = winreg.OpenKey(key, winreg.EnumKey(key, i))
                        name = winreg.QueryValueEx(skey, "DisplayName")[0]
                        path = winreg.QueryValueEx(skey, "InstallLocation")[0]
                        if name and path: uninstall_map[self._clean_name(name)] = path
                    except: continue
            except: pass

        for root in [r"SOFTWARE\WOW6432Node\Origin Games", r"SOFTWARE\Electronic Arts\EA Games"]:
            try:
                hkey = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
                for i in range(1024):
                    try:
                        launch_id = winreg.EnumKey(hkey, i)
                        game_key = winreg.OpenKey(hkey, launch_id)
                        name = winreg.QueryValueEx(game_key, "DisplayName")[0]
                        path = uninstall_map.get(self._clean_name(name), "Unknown")
                        
                        if path != "Unknown" and self._is_valid_game_folder(path):
                            games.append(Game(name, 'EA', launch_id, path))
                            found_paths.add(self._clean_name(name))
                        elif self._has_start_menu_shortcut(name):
                            games.append(Game(name, 'EA', launch_id, "Unknown"))
                            found_paths.add(self._clean_name(name))
                    except: continue
            except: pass

        # Method 2: Physical Folder Scan (For FC 26 and others missing from Registry)
        # Check standard installation folders
        ea_folders = [
            r"C:\Program Files\EA Games",
            r"C:\Program Files (x86)\EA Games",
            r"C:\Program Files\Origin Games",
            r"C:\Program Files (x86)\Origin Games"
        ]

        for folder in ea_folders:
            if os.path.isdir(folder):
                for game_dir in os.listdir(folder):
                    clean_dir = self._clean_name(game_dir)
                    
                    # If we haven't found this game yet (via registry)
                    if clean_dir not in found_paths:
                        full_path = os.path.join(folder, game_dir)
                        if self._is_valid_game_folder(full_path):
                            # We use the folder name as the Game Name
                            # Launch ID is tricky for unknown folder games. 
                            # We set it to the path so it opens the folder/exe.
                            games.append(Game(game_dir, 'EA', full_path, full_path))
                            found_paths.add(clean_dir)

        return games
    
    # --- MANUAL ---
    def _load_manual_games(self) -> List[Game]:
        games = []
        try:
            path = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub', 'manual_games.json')
            if os.path.exists(path):
                with open(path, 'r') as f:
                    for n, p in json.load(f).items():
                        if os.path.exists(p): games.append(Game(n, 'Other Games', None, p))
        except: pass
        return games