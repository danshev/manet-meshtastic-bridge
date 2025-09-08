import hashlib
import json
import socket
import struct
import time

import takproto

from config import EUD_MULTICAST_GROUP, EUD_LISTEN_PORT, SEND_TEXT_TOO, SEND_POSITION
from utils import log, encode_varint
from cot import parse_cot
from health_probe import ip_lock, ip_unhealthy
from mesh_interface import rate_ok, dedup_ok

def cot_ingress_loop(mesh):
    import struct
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', EUD_LISTEN_PORT))
    mreq = struct.pack("4sl", socket.inet_aton(EUD_MULTICAST_GROUP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    log(f"[bridge] listening UDP multicast {EUD_MULTICAST_GROUP}:{EUD_LISTEN_PORT}")
    while True:
        data, addr = sock.recvfrom(65535)
        log(f"[bridge] received from {addr} len={len(data)}")
        payload = data
        is_proto = False
        xml_bytes = None
        if len(data) > 3 and data[0] == 0xbf and data[2] == 0xbf:
            version = data[1]
            payload = data[3:]
            if version == 1:
                is_proto = True
            elif version == 0:
                is_proto = False
                xml_bytes = payload
        else:
            # Assume raw XML
            is_proto = False
            xml_bytes = data
        with ip_lock:
            fb = ip_unhealthy
        if not fb:
            if is_proto:
                # Forward as stream proto
                msg_buf = b'\xbf' + encode_varint(len(payload)) + payload
                tak_server.send(msg_buf)
            else:
                # Forward as raw XML
                cot_out.send(xml_bytes)
        else:
            try:
                if is_proto:
                    tak_msg = takproto.parse_proto(payload)
                    xml = takproto.proto2xml(tak_msg)
                    parsed = parse_cot(xml.encode('utf-8'))
                else:
                    parsed = parse_cot(xml_bytes)
                if parsed:
                    h = hashlib.sha256(json.dumps(parsed).encode()).hexdigest()
                    if not dedup_ok(h): continue
                    if not rate_ok(parsed["uid"]): continue
                    j = {
                        "u": parsed["uid"],
                        "ct": parsed["type"],  # CoT type
                        "lat": round(parsed["lat"], 6),
                        "lon": round(parsed["lon"], 6),
                        "a": int(parsed["alt"]),
                        "t": parsed["ts"]  # Timestamp
                    }
                    if parsed.get("callsign"):
                        j["c"] = parsed["callsign"]
                    if parsed.get("group"):
                        j["g"] = {"n": parsed["group"]["name"], "r": parsed["group"]["role"]}
                    
                    if parsed["chat"] is None:
                        j["k"] = "p"  # pli
                        if SEND_POSITION:
                            mesh.send_position(parsed["lat"], parsed["lon"], parsed["alt"], parsed["ts"])
                        if SEND_TEXT_TOO:
                            mesh.send_text(j)  # auto-JSON + size trim handled inside send_text
                    else:
                        j.update({
                            "k": "c",  # chat
                            "f": parsed["chat"]["from"],
                            "d": parsed["chat"]["to"],
                            "m": parsed["chat"]["message"],
                        })
                        if "chatroom" in parsed["chat"]:  # Add "rc" only if present (saves space for group chats)
                            j["rc"] = parsed["chat"]["chatroom"]
                        mesh.send_text(j)
            except Exception as e:
                log(f"[bridge] parse error: {e}")