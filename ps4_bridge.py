import hid
import threading
import time
import logging

# Attempt to import vgamepad. If the driver or library is missing, 
# we set a flag instead of crashing the whole app.
try:
    import vgamepad as vg
    VIGEM_INSTALLED = True
except Exception:
    VIGEM_INSTALLED = False

class PS4Bridge:
    def __init__(self):
        self.running = False
        self.virtual_pad = None
        self.device = None
        self.thread = None
        
        # OFFSETS
        self.idx_stick = 1
        self.idx_btn = 5
        self.idx_trig = 8
        self.idx_bat = 30
        self.mode = "USB"

        # SETTINGS
        self.deadzone = 0.1
        self.sensitivity = 1.0
        
        # STATE
        self.battery_level = 0
        self.lock = threading.Lock()
        
        # CALLBACK
        self.on_disconnect = None

    def update_settings(self, deadzone, sensitivity):
        self.deadzone = float(deadzone)
        self.sensitivity = float(sensitivity)

    def get_battery(self): 
        return self.battery_level

    def _update_battery(self, raw_val):
        """
        Sony DS4 reports 0x00-0x08 for levels, 0x0B for charging.
        This is an estimated mapping based on raw HID reports.
        """
        try:
            # Mask to get the lower 4 bits which usually contain battery info
            level = raw_val & 0x0F
            if level <= 0x08:
                # 0x08 is 100%, 0x00 is 0% (roughly)
                self.battery_level = int((level / 8.0) * 100)
            elif level >= 0x0B:
                # Charging or Full
                self.battery_level = 100
            else:
                self.battery_level = 0
        except Exception as e:
            logging.error(f"Battery calculation error: {e}")
            self.battery_level = 0

    def start(self):
        if not VIGEM_INSTALLED:
            logging.error("ViGEmBus Driver not found. Controller bridge disabled.")
            return False

        if self.running: 
            return True

        try:
            # SCAN for Sony Controller (Vendor ID 1356)
            target = None
            for d in hid.enumerate():
                if d['vendor_id'] == 1356:
                    target = d
                    break
            
            if not target: 
                return False

            # CONNECT
            self.device = hid.device()
            self.device.open(target['vendor_id'], target['product_id'])
            self.device.set_nonblocking(True)

            # VIRTUAL XBOX (Requires ViGEmBus Driver)
            self.virtual_pad = vg.VX360Gamepad()
            
            self.running = True
            self.thread = threading.Thread(target=self._bridge_loop, daemon=True)
            self.thread.start()
            return True
        except Exception as e:
            logging.error(f"Bridge Start Error: {e}")
            return False

    def stop(self):
        if not self.running: 
            return  # E1: Prevent double-trigger
        self.running = False
        
        if self.thread: 
            self.thread.join(timeout=1)
        
        with self.lock:
            if self.device: 
                try:
                    self.device.close()
                except:
                    pass
                self.device = None
        
        self.virtual_pad = None
        logging.info("PS4 Bridge Stopped")
        
        if self.on_disconnect:
            self.on_disconnect()

    def _apply_deadzone(self, val):
        if abs(val) < self.deadzone: 
            return 0.0
        return max(min(val * self.sensitivity, 1.0), -1.0)

    def _set_offsets(self, report_id):
        if report_id == 17: # Bluetooth Mode
            self.mode = "BT"
            self.idx_stick = 3
            self.idx_btn = 7
            self.idx_trig = 10
            self.idx_bat = 32
        else: # USB Mode
            self.mode = "USB"
            self.idx_stick = 1
            self.idx_btn = 5
            self.idx_trig = 8
            self.idx_bat = 30

    def _bridge_loop(self):
        logging.info("PS4 Bridge Driver Loop Running")
        offsets_calibrated = False

        while self.running:
            try:
                # Read raw HID data from the controller
                report = self.device.read(64)
                
                if not report:
                    time.sleep(0.001)
                    continue

                if not offsets_calibrated:
                    self._set_offsets(report[0])
                    offsets_calibrated = True

                # 1. JOYSTICKS
                i = self.idx_stick
                lx = (report[i] - 128) / 128.0
                ly = (report[i+1] - 128) / 128.0
                rx = (report[i+2] - 128) / 128.0
                ry = (report[i+3] - 128) / 128.0
                
                self.virtual_pad.left_joystick_float(
                    x_value_float=self._apply_deadzone(lx), 
                    y_value_float=self._apply_deadzone(-ly)
                )
                self.virtual_pad.right_joystick_float(
                    x_value_float=self._apply_deadzone(rx), 
                    y_value_float=self._apply_deadzone(-ry)
                )

                # 2. TRIGGERS
                t = self.idx_trig
                l2 = report[t] / 255.0
                r2 = report[t+1] / 255.0
                self.virtual_pad.left_trigger_float(value_float=l2)
                self.virtual_pad.right_trigger_float(value_float=r2)

                # 3. BUTTONS (Face & D-Pad)
                b = self.idx_btn
                val = report[b]
                
                # Cross, Circle, Square, Triangle
                if val & 16: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_X)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_X)
                if val & 32: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
                if val & 64: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_B)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_B)
                if val & 128: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)

                # D-Pad (Hat switch)
                hat = val & 15
                self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
                self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
                self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
                self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
                
                if hat == 0: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
                elif hat == 1: 
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
                elif hat == 2: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
                elif hat == 3: 
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
                elif hat == 4: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
                elif hat == 5: 
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
                elif hat == 6: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
                elif hat == 7: 
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
                    self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)

                # 4. SECONDARY (Shoulders, Start, Select, Stick Clicks)
                val2 = report[b+1]
                if val2 & 1: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER)
                if val2 & 2: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER)
                if val2 & 16: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK)
                if val2 & 32: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_START)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_START)
                if val2 & 64: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB)
                if val2 & 128: self.virtual_pad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB)
                else: self.virtual_pad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB)

                # 5. BATTERY
                if len(report) > self.idx_bat:
                    self._update_battery(report[self.idx_bat])

                self.virtual_pad.update()
                time.sleep(0.001)

            except OSError as e:
                # Often happens when Steam or another app takes control
                logging.warning(f"Controller access lost: {e}. Ensure Steam is closed.")
                self.running = False
                break
            except Exception as e:
                logging.error(f"Bridge error: {e}")
                self.running = False
                break

        # Cleanup hardware and virtual pad when loop ends
        self.stop()

# Singleton instance for the app
bridge = PS4Bridge()