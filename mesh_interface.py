import json
import threading
import time

from meshtastic.serial_interface import SerialInterface

from config import SERIAL_DEV, MESH_MIN_INTERVAL_SEC, DEDUP_WINDOW_SEC
from utils import log

last_send_by_uid = {}
seen_ids = {}

def rate_ok(uid):
    now = time.time(); last = last_send_by_uid.get(uid or "unknown",0)
    if now - last < MESH_MIN_INTERVAL_SEC: return False
    last_send_by_uid[uid or "unknown"] = now; return True

def dedup_ok(h):
    now = time.time()
    for k,t in list(seen_ids.items()):
        if now - t > DEDUP_WINDOW_SEC: del seen_ids[k]
    if h in seen_ids: return False
    seen_ids[h] = now; return True

class Mesh:
    def __init__(self, dev):
        self.dev = dev
        self.iface = None
        self.lock = threading.Lock()

    def connect(self):
        while True:
            try:
                log(f"[mesh] connecting {self.dev} ...")
                self.iface = SerialInterface(devPath=self.dev, noProto=False, debugOut=False)
                log("[mesh] connected.")
                return
            except Exception as e:
                log(f"[mesh] connect failed: {e}; retrying in 1s"); time.sleep(1)

    def send_position(self, lat, lon, alt, ts):
        with self.lock:
            try: self.iface.sendPosition(lat=lat, lon=lon, alt=alt, timeSec=int(ts)); return True
            except Exception as e: log(f"[mesh] position send error: {e}"); return False

    def send_text(self, data, max_len=180):
        """
        Send either a raw string or a dict/list (auto-JSON). Trims to max_len bytes.
        """
        if isinstance(data, (dict, list)):
            txt = json.dumps(data, separators=(",", ":"))
        else:
            txt = str(data)
        if len(txt) > max_len:
            txt = txt[:max_len]
        with self.lock:
            try:
                self.iface.sendText(txt)
                return True
            except Exception as e:
                log(f"[mesh] text send error: {e}")
                return False