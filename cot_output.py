import socket

from config import is_local, EUD_MULTICAST_GROUP, EUD_LISTEN_PORT, TCP_PROBE_TIMEOUT
from utils import log

class CotOut:
    def __init__(self, tak_server):
        self.tak_server = tak_server

    def send(self, b: bytes):
        if not is_local:
            log("[cot] Broadcasting (local multicast)")
            try:
                udp_send_sock.sendto(b, (EUD_MULTICAST_GROUP, EUD_LISTEN_PORT))
                log("[cot] Broadcast raw XML to local multicast")
            except Exception as e:
                log(f"[cot] error during local multicast: {e}")

        try:
            # NEW: Use a new socket per send to simulate separate EUD connections (OTS assumes 1 conn = 1 UID)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TCP_PROBE_TIMEOUT)
            sock.connect((self.tak_server.host, self.tak_server.port))
            sock.sendall(b)
            log("[cot] Sent raw XML via new socket")
            sock.close()
        except Exception as e:
            log(f"[cot] error sending to OpenTAK Server: {e}")