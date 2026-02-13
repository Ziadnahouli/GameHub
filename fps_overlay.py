import tkinter as tk
import time
import socket
import threading
import sys
import ctypes

def make_transparent(window):
    try:
        window.attributes('-alpha', 0.8)  # Semi-transparent background
        window.attributes('-topmost', True) # Always on top
        valid_color = "#333333" # Dark grey background
        window.configure(bg=valid_color)
        
        # Windows specific: make it click-through (optional, but good for overlays)
        # Using ctypes to set WS_EX_TRANSPARENT + WS_EX_LAYERED
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20) # GWL_EXSTYLE
        style = style | 0x00080000 | 0x00000020 # WS_EX_LAYERED | WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
    except Exception as e:
        print(f"Transparency failed: {e}")

class FPSOverlayApp(tk.Tk):
    def __init__(self):
        super().__init__()
        
        # Window Setup
        self.overrideredirect(True) # No title bar
        self.geometry("140x50+20+20") # Top Left
        self.attributes('-topmost', True)
        self.config(bg='black')
        self.attributes('-transparentcolor', 'black') # Make black fully transparent
        
        # Label
        self.fps_label = tk.Label(self, text="FPS: --", font=("Segoe UI", 16, "bold"), fg="#00ff00", bg="black")
        self.fps_label.pack(expand=True, fill='both')
        
        # Networking for updates
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(('127.0.0.1', 8899))
            self.sock.setblocking(False)
        except OSError:
            # Port already in use, likely another overlay instance is running.
            # We exit quietly to avoid user-facing tracebacks.
            sys.exit(0)
        
        # Start update loop
        self.update_fps()

    def update_fps(self):
        try:
            data, addr = self.sock.recvfrom(1024)
            fps_val = data.decode('utf-8')
            self.fps_label.config(text=f"FPS: {fps_val}")
        except BlockingIOError:
            pass
        except Exception as e:
            print(f"Error: {e}")
            
        self.after(500, self.update_fps) # Check every 500ms

if __name__ == "__main__":
    app = FPSOverlayApp()
    app.mainloop()
