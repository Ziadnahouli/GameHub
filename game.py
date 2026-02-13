# game.py
import os
import json
from typing import Optional, Union
from dataclasses import dataclass, field, fields, asdict

@dataclass
class Game:
    name: str
    source: str
    launch_id: Union[str, int, None]
    install_path: Optional[str] = None
    executable_name: Optional[str] = None 
    
    # --- User Data ---
    favorite: bool = False
    hidden: bool = False
    last_played: Optional[float] = None
    playtime_seconds: int = 0
    tags: str = "[]"

    # --- Metadata ---
    grid_image_url: Optional[str] = None

    # --- Performance Data ---
    avg_fps: str = ""
    best_ping: str = ""

    # --- Properties ---
    launch_args: str = ""
    controller_type: str = "None"
    
    # --- Automation ---
    auto_bridge: bool = False
    focus_mode: bool = False
    
    # --- Update Detection ---
    installed_version: str = ""
    installed_build: str = ""
    last_updated: float = 0
    update_status: str = "UNKNOWN"  # UP_TO_DATE, UPDATE_AVAILABLE, UPDATING, UNKNOWN, NOT_INSTALLED
    update_source: str = ""
    last_update_check: float = 0

    def __post_init__(self):
        if self.name:
            self.name = self.name.strip()

    def get_launch_command(self) -> Optional[str]:
        if self.source == 'Steam': 
            return f"steam://run/{self.launch_id}"
        elif self.source == 'Epic Games': 
            return f"com.epicgames.launcher://apps/{self.launch_id}?action=launch&silent=true"
        elif self.source == 'EA': 
            return f"origin://launchgame/{self.launch_id}"
        elif self.source == 'Other Games': 
            return self.install_path
        return None
    
    @property
    def unique_id(self) -> str:
        # Create a consistent ID based on Source + Name
        return f"{self.source}|{self.name}"

    def to_dict(self) -> dict:
        d = asdict(self)
        # Ensure the unique_id is sent to the frontend
        d['unique_id'] = self.unique_id
        return d
    
    @staticmethod
    def from_dict(data: dict) -> 'Game':
        class_fields = {f.name for f in fields(Game)}
        filtered_data = {k: v for k, v in data.items() if k in class_fields}
        return Game(**filtered_data)