#!/usr/bin/env python3
import glob
import os
import socket
import sys
import threading
import time

from pubsub import pub

from config import is_local, SERIAL_DEV, TAK_FWD_HOST, TAK_FWD_PORT, EUD_MULTICAST_GROUP
from utils import log
from health_probe import tcp_probe
from tak_server import TAKServer
from mesh_interface import Mesh
from cot_output import CotOut
from message_handlers import on_mesh_receive
from ingress import cot_ingress_loop

def main():
    log(f"[info] OpenTAK Server detected locally: {is_local}")
    
    if not is_local:
        # health thread
        threading.Thread(target=tcp_probe, daemon=True).start()

    # serial device pick
    dev = SERIAL_DEV if os.path.exists(SERIAL_DEV) else (sorted(glob.glob("/dev/ttyACM*")) + [None])[0]
    if not dev:
        log("[mesh] no serial device found; plug in RAK4631"); sys.exit(1)

    # connect radio
    global mesh
    mesh = Mesh(dev)
    mesh.connect()

    # wire pubsub handlers
    pub.subscribe(on_mesh_receive, "meshtastic.receive")
    log("[mesh] subscribed to pubsub topic 'meshtastic.receive'")

    # OpenTAK Server
    global tak_server
    tak_server = TAKServer(TAK_FWD_HOST, TAK_FWD_PORT)

    # CoT out
    global cot_out
    cot_out = CotOut(tak_server)

    # UDP send sock for reverse direction
    global udp_send_sock
    udp_send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    udp_send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, b'\x02')

    if not is_local:
        # TAK ingress in background
        threading.Thread(target=cot_ingress_loop, args=(mesh,), daemon=True).start()

    # keep alive
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass

# instantiate globals
mesh = None
tak_server = None
cot_out = None
udp_send_sock = None

if __name__ == "__main__":
    main()