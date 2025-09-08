import socket
import threading

from config import TCP_PROBE_TIMEOUT, is_local, EUD_MULTICAST_GROUP, EUD_LISTEN_PORT
from utils import log, encode_varint

class TAKServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self.read_thread = None

    def connect(self):
        with self.lock:
            if self.sock:
                return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TCP_PROBE_TIMEOUT)
            sock.connect((self.host, self.port))
            with self.lock:
                self.sock = sock
                if not is_local:  # Only start read_loop if not local, as local might not need bidirectional from server
                    self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
                    self.read_thread.start()
            return True
        except:
            return False

    def send(self, payload: bytes):
        if not self.connect():
            log("[server] Failed to connect to OpenTAK Server")
            return False
        try:
            self.sock.sendall(payload)
            log("[server] Successfully sent payload to OpenTAK Server")
            return True
        except:
            log("[server] Failed to send payload, disconnecting")
            self.disconnect()
            return False

    def disconnect(self):
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
                self.sock = None

    def read_loop(self):
        buf = b''
        while True:
            try:
                data = self.sock.recv(65535)
                if not data:
                    break
                buf += data
                while len(buf) > 0:
                    if buf[0] != 0xbf:
                        buf = buf[1:]
                        continue
                    varint_pos = 1
                    length = 0
                    shift = 0
                    while varint_pos < len(buf):
                        byte = buf[varint_pos]
                        length |= (byte & 0x7f) << shift
                        shift += 7
                        varint_pos += 1
                        if not (byte & 0x80):
                            break
                    if varint_pos + length > len(buf):
                        break
                    msg_buf = buf[0:varint_pos + length]
                    payload = buf[varint_pos:varint_pos + length]
                    buf = buf[varint_pos + length:]
                    # Forward to UDP multicast (convert to Mesh format)
                    mesh_header = b'\xbf\x01\xbf' + payload
                    udp_send_sock.sendto(mesh_header, (EUD_MULTICAST_GROUP, EUD_LISTEN_PORT))
            except Exception as e:
                log(f"[server] read error: {e}")
                break
        self.disconnect()