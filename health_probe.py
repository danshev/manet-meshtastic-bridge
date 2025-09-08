import socket
import threading
import time

from config import HEALTH_HOST, HEALTH_PORT, TCP_PROBE_TIMEOUT, HEALTH_INTERVAL, HEALTH_FAILS_TRIGGER, HEALTH_RECOVER_COUNT
from utils import log

ip_lock = threading.Lock()
ip_unhealthy = False
_fail = 0
_recover = 0

def tcp_probe():
    global ip_unhealthy, _fail, _recover
    while True:
        healthy = False
        if HEALTH_HOST:
            try:
                s = socket.create_connection((HEALTH_HOST, HEALTH_PORT), TCP_PROBE_TIMEOUT)
                s.close()
                healthy = True
            except Exception:
                pass
        with ip_lock:
            if healthy:
                _recover += 1; _fail = 0
                if ip_unhealthy and _recover >= HEALTH_RECOVER_COUNT:
                    ip_unhealthy = False; _recover = 0
                    log("[health] IP recovered; revert to IP-preferred.")
            else:
                _fail += 1; _recover = 0
                if not ip_unhealthy and _fail >= HEALTH_FAILS_TRIGGER:
                    ip_unhealthy = True
                    log("[health] IP degraded; enabling Meshtastic fallback.")
        time.sleep(HEALTH_INTERVAL)