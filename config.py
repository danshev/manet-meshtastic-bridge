import os
import glob

# TAK ingress (UDP multicast from EUDs)
EUD_MULTICAST_GROUP = os.getenv("EUD_MULTICAST_GROUP", "239.2.3.1")
EUD_LISTEN_PORT = int(os.getenv("EUD_LISTEN_PORT", "6969"))

home_base = '/home'
is_local = False
if os.path.exists(home_base):
    for user_dir in os.listdir(home_base):
        user_home = os.path.join(home_base, user_dir)
        if os.path.isdir(user_home):
            ots_path = os.path.join(user_home, 'ots')
            if os.path.isdir(ots_path):
                is_local = True
                break

# Primary TAK forward (TCP to OpenTAK Server)            
TAK_FWD_HOST = os.getenv("TAK_FWD_HOST", "127.0.0.1" if is_local else "")
TAK_FWD_PORT = int(os.getenv("TAK_FWD_PORT", "8089"))  # Updated to TCP default

# Health probe of OpenTAK Server (TCP)
HEALTH_HOST = os.getenv("HEALTH_HOST", TAK_FWD_HOST)
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8089"))
HEALTH_INTERVAL = float(os.getenv("HEALTH_INTERVAL", "3.0"))
HEALTH_FAILS_TRIGGER = int(os.getenv("HEALTH_FAILS_TRIGGER", "3"))
HEALTH_RECOVER_COUNT = int(os.getenv("HEALTH_RECOVER_COUNT", "3"))
TCP_PROBE_TIMEOUT = float(os.getenv("TCP_PROBE_TIMEOUT", "1.0"))

# Meshtastic serial
SERIAL_DEV = os.getenv("SERIAL_DEV", "/dev/meshtastic0")

# Fallback send control
MESH_MIN_INTERVAL_SEC = float(os.getenv("MESH_MIN_INTERVAL", "5.0"))
DEDUP_WINDOW_SEC = float(os.getenv("DEDUP_WINDOW", "120.0"))
SEND_TEXT_TOO = os.getenv("SEND_TEXT_TOO", "1") == "1"
SEND_POSITION = os.getenv("SEND_POSITION", "1") == "1"

# Defaults for generated CoT from Meshtastic
DEFAULT_UID = os.getenv("DEFAULT_UID", "MESH-{from}")
DEFAULT_TYPE = os.getenv("DEFAULT_TYPE", "a-f-G-U-C")
DEFAULT_CHAT_TYPE = os.getenv("DEFAULT_CHAT_TYPE", "b-t-f")  # Added for chat
STALE_SECS = int(os.getenv("STALE_SECS", "60"))