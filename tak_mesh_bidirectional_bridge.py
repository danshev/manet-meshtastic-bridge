#!/usr/bin/env python3
# TAK <-> Meshtastic bidirectional bridge using pubsub for RX events.
# - TAK ingress (UDP multicast) -> forward to TAK server (TCP) as primary
# - Health probe; on degradation also send compact JSON + Position over Meshtastic
# - Meshtastic RX via pubsub -> rebuild CoT <event> and send to TAK server (TCP)

import glob
import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element, SubElement, tostring

import takproto

# ======================= Config via env =======================
# TAK ingress (UDP multicast from EUDs)
EUD_MULTICAST_GROUP = os.getenv("EUD_MULTICAST_GROUP", "239.2.3.1")
EUD_LISTEN_PORT = int(os.getenv("EUD_LISTEN_PORT", "6969"))

# Primary TAK forward (TCP to server)
is_local = os.path.isdir('ots')
TAK_FWD_HOST = os.getenv("TAK_FWD_HOST", "127.0.0.1" if is_local else "")
TAK_FWD_PORT = int(os.getenv("TAK_FWD_PORT", "8089"))  # Updated to TCP default

# Health probe of TAK server (TCP)
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

# ======================= Utilities ===========================
def log(msg: str):
    print(datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z"), msg, flush=True)

def iso_z(ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    elif isinstance(ts,(int,float)):
        ts = datetime.fromtimestamp(ts, tz=timezone.utc)
    return ts.replace(microsecond=0).isoformat().replace("+00:00","Z")

def encode_varint(value: int) -> bytes:
    data = b''
    while value >= 0x80:
        data += bytes([(value & 0x7f) | 0x80])
        value >>= 7
    data += bytes([value])
    return data

def make_cot(
    uid, typ, lat, lon, alt=0.0, ts=None, *,
    chat=None,
    callsign=None,
    group_name=None,
    group_role=None
):
    now = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc)
    start = iso_z(now)
    stale = iso_z(now + timedelta(seconds=STALE_SECS))
    
    how = "m-g" if not chat else "h-g-i-g-o"

    ev = Element("event", {
        "version": "2.0",
        "type": typ,
        "uid": uid,
        "time": start,
        "start": start,
        "stale": stale,
        "how": how
    })
    hae = "9999999.0" if alt == 0 else f"{float(alt):.1f}"
    SubElement(ev, "point", {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "hae": hae,
        "ce": "9999999.0",
        "le": "9999999.0"
    })
    detail = SubElement(ev, "detail")

    if chat:
        # NEW: Proper GeoChat structure; eliminate unnecessary subelements
        sender_uid = chat.get("from", "")
        recip_id = chat.get("to", "All Chat Rooms")
        message = chat.get("message", "")
        is_group = recip_id == "All Chat Rooms"
        chatroom = "All Chat Rooms" if is_group else chat.get("rc", recip_id)  # Use "rc" if provided, else fallback to recip_id
        sender_callsign = callsign or "MeshUser"  # Default if no callsign
        message_id = str(uuid.uuid4()).upper()

        # Update uid if not provided (omit "u" in JSON)
        if not uid:
            uid = f"GeoChat.{sender_uid}.{recip_id}.{message_id}"
            ev.attrib["uid"] = uid  # Update event uid

        geo_chat = SubElement(detail, "__chat", {
            "parent": "RootContactGroup",
            "groupOwner": "false",
            "messageId": message_id,
            "chatroom": chatroom,
            "id": recip_id,
            "senderCallsign": sender_callsign
        })
        uid1 = "" if is_group else recip_id
        SubElement(geo_chat, "chatgrp", {
            "uid0": sender_uid,
            "uid1": uid1,
            "id": recip_id
        })
        SubElement(detail, "link", {
            "uid": sender_uid,
            "type": DEFAULT_TYPE,
            "relation": "p-p"
        })
        remarks = SubElement(detail, "remarks", {
            "source": f"BAO.F.MESH.{sender_uid}",
            "to": recip_id,
            "time": start
        })
        remarks.text = message
        if not is_group:
            marti = SubElement(detail, "marti")
            SubElement(marti, "dest", {"uid": recip_id})

        # Optionally add group if provided (real examples omit, but keep if wanted)
        if group_name or group_role:
            group_name = group_name or "Blue"
            group_role = group_role or "Team Member"
            SubElement(detail, "__group", {"name": group_name, "role": group_role})

    else:
        # Existing PLI logic (unchanged, but kept for reference)
        if not group_name:
            group_name = "Blue"
        if not group_role:
            group_role = "Team Member"
        SubElement(detail, "__group", {"name": str(group_name), "role": str(group_role)})
        contact_attrs = {"endpoint": "*:-1:stcp"}
        if callsign:
            contact_attrs["callsign"] = str(callsign)
        SubElement(detail, "contact", contact_attrs)
        SubElement(detail, "precisionlocation", {"geopointsrc": "GPS", "altsrc": "???"})
        SubElement(detail, "status", {"battery": "100"})
        SubElement(detail, "takv", {"device": "RAK4631", "platform": "Meshtastic", "os": "1", "version": "1.0"})
        SubElement(detail, "track", {"speed": "0.0", "course": "0.0"})
        if callsign:
            SubElement(detail, "uid", {"Droid": str(callsign)})
        if callsign:
            SubElement(detail, "voice", {"iceUserID": "", "whisperUserID": "", "takCallsign": str(callsign), "isSpeaking": "false"})

    # XML header match (use "UTF-8" to match one real example)
    xml_header = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    return xml_header + tostring(ev, encoding="utf-8", method='xml')


# ======================= Health probe ========================
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

# ======================= TAK Server Connection ===============
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
            log("[server] Failed to connect to TAK server")
            return False
        try:
            self.sock.sendall(payload)
            log("[server] Successfully sent payload to TAK server")
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

# ======================= CoT Parsing 
def parse_cot(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
        if root.tag != "event":
            return None

        uid = root.attrib.get("uid", "")
        cot_type = root.attrib.get("type", "")
        time_str = root.attrib.get("time") or root.attrib.get("stale") or ""
        ts = int(time.time())
        if time_str:
            try:
                ts = int(datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp())
            except Exception:
                pass

        lat = lon = alt = 0.0
        point = root.find("point")
        if point is not None:
            lat = float(point.attrib.get("lat", "0"))
            lon = float(point.attrib.get("lon", "0"))
            alt = float(point.attrib.get("hae", "0"))

        # Pull optional metadata
        callsign = None
        group = None
        chat = None

        detail = root.find("detail")
        if detail is not None:
            c_el = detail.find("contact")
            if c_el is not None:
                callsign = c_el.attrib.get("callsign")

            g_el = detail.find("__group")
            if g_el is not None:
                group = {
                    "name": g_el.attrib.get("name"),
                    "role": g_el.attrib.get("role")
                }

            # Existing simple <chat> parsing (kept for backward compat)
            chat_el = detail.find("chat")
            if chat_el is not None:
                chat = {
                    "from": chat_el.attrib.get("from", ""),
                    "to": chat_el.attrib.get("to", ""),
                    "message": chat_el.attrib.get("message", "")
                }

            # NEW: Parse real GeoChat (type "b-t-f" with <__chat> and <remarks>)
            if cot_type == "b-t-f" and chat is None:  # Avoid override if <chat> present
                geo_chat_el = detail.find("__chat")
                if geo_chat_el is not None:
                    sender_callsign = geo_chat_el.attrib.get("senderCallsign")
                    chatroom = geo_chat_el.attrib.get("chatroom")
                    recip_id = geo_chat_el.attrib.get("id")
                    message_id = geo_chat_el.attrib.get("messageId")

                    chatgrp = geo_chat_el.find("chatgrp")
                    sender_uid = chatgrp.attrib.get("uid0") if chatgrp else ""
                    recip_uid = chatgrp.attrib.get("uid1") if chatgrp else recip_id

                    remarks = detail.find("remarks")
                    message = remarks.text if remarks else ""
                    to = remarks.attrib.get("to") if remarks else recip_id
                    source = remarks.attrib.get("source") if remarks else ""

                    # Fallback sender UID from remarks source if missing
                    if not sender_uid and source.startswith("BAO.F.ATAK."):
                        sender_uid = source.split(".")[-1]

                    chat = {
                        "from": sender_uid,
                        "to": to,
                        "message": message,
                        "chatroom": chatroom,  # For "rc" in JSON
                    }
                    if sender_callsign:  # Override callsign with senderCallsign
                        callsign = sender_callsign

        return {
            "uid": uid,
            "type": cot_type,
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "ts": ts,
            "chat": chat,
            "callsign": callsign,
            "group": group
        }
    except Exception:
        return None


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

# ======================= Meshtastic TX/RX ====================
from meshtastic.serial_interface import SerialInterface
from pubsub import pub


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

# TAK out (reverse to server)
class CotOut:
    def __init__(self, tak_server):
        self.tak_server = tak_server

    def send(self, b: bytes):
        log("[cot] Preparing to send CoT to TAK server")
        if not is_local:
            with ip_lock:
                if ip_unhealthy:
                    log("[cot] Skipped send to server; IP unhealthy")
                    return
        try:
            # NEW: Use a new socket per send to simulate separate EUD connections (OTS assumes 1 conn = 1 UID)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TCP_PROBE_TIMEOUT)
            sock.connect((self.tak_server.host, self.tak_server.port))
            sock.sendall(b)
            log("[cot] Sent raw XML via new socket")
            sock.close()
        except Exception as e:
            log(f"[cot] Error sending to server: {e}")

def pkt_sender_uid(pkt):
    frm = pkt.get("from") or pkt.get("fromId") or pkt.get("fromIdShort")
    return (DEFAULT_UID.replace("{from}", str(frm))) if frm else "MESH-UNKNOWN"

def decode_position(pkt):
    d = pkt.get("decoded") or {}
    p = d.get("position") or {}
    try:
        lat = float(p.get("latitude"))
        lon = float(p.get("longitude"))
    except (TypeError, ValueError):
        return None
    alt = float(p.get("altitude", 0))
    ts = int(p.get("time", time.time()))
    return {"lat": lat, "lon": lon, "alt": alt, "ts": ts}

def decode_json_text(pkt):
    log("[rx] Attempting to decode JSON from Meshtastic text")
    d = pkt.get("decoded") or {}
    txt = d.get("text")
    if not txt:
        log("[rx] No text found in decoded packet")
        return None
    log(f"[rx] Extracted text: {txt[:100]}...")
    try:
        j = json.loads(txt)
        log("[rx] Successfully parsed JSON")
    except Exception as e:
        log(f"[rx] JSON parsing failed: {e}")
        return None
    if "k" not in j:
        log("[rx] JSON missing 'k' key")
        return None
    k_val = j["k"]
    if k_val == "p":
        out_type = "pli"
    elif k_val == "c":
        out_type = "chat"
    else:
        log(f"[rx] Unknown JSON kind: {k_val}")
        return None
    out = {"type": out_type}
    log(f"[rx] Detected type: {out['type']}")
    if isinstance(j.get("g"), dict):
        out["group"] = {
            "name": j["g"].get("n"),
            "role": j["g"].get("r"),
        }
        log("[rx] Added group information")
    if j.get("c"):
        out["callsign"] = j.get("c")
        log("[rx] Added callsign")
    if out["type"] == "pli":
        if "lat" not in j or "lon" not in j:
            log("[rx] PLI JSON missing lat or lon")
            return None
        out.update({
            "uid": j.get("u"),
            "t": j.get("ct"),
            "lat": float(j["lat"]),
            "lon": float(j["lon"]),
            "alt": float(j.get("a", 0)),
            "ts": int(j.get("t", time.time())),
        })
        log("[rx] Successfully decoded PLI JSON")
    elif out["type"] == "chat":
        out.update({
            "uid": j.get("u"),
            "t": j.get("ct"),
            "from": j.get("f"),
            "to": j.get("d"),
            "message": j.get("m"),
            "lat": float(j.get("lat", 0)),
            "lon": float(j.get("lon", 0)),
            "ts": int(j.get("t", time.time())),
            "rc": j.get("rc"),  # Optional recipient callsign
        })
        log("[rx] Successfully decoded CHAT JSON")
    return out

# pubsub RX handler
def on_mesh_receive(packet, interface):
    log("[rx] Received Meshtastic packet via pubsub")
    try:
        pos = decode_position(packet)
        if pos:
            log("[rx] Decoded as native position")
            uid = pkt_sender_uid(packet)
            typ = DEFAULT_TYPE
            cot_xml = make_cot(uid, typ, pos["lat"], pos["lon"], pos["alt"], pos["ts"])
            cot_out.send(cot_xml)
            log(f"[cot] sent from POSITION_APP uid={uid} lat={pos['lat']:.6f} lon={pos['lon']:.6f}")
            return

        dat = decode_json_text(packet)
        if dat:
            log(f"[rx] Processing decoded JSON of type {dat['type']}")
            if dat["type"] == "pli":
                uid = dat.get("uid") or pkt_sender_uid(packet)
                typ = dat.get("t") or DEFAULT_TYPE
                log("[rx] Creating CoT XML for PLI")
                cot_xml = make_cot(
                    uid, typ, dat["lat"], dat["lon"], dat["alt"], dat["ts"],
                    callsign=dat.get("callsign"),
                    group_name=(dat.get("group") or {}).get("name"),
                    group_role=(dat.get("group") or {}).get("role"),
                )
                log(f"[rx] Created CoT XML for PLI: {cot_xml[:200].decode('utf-8', 'ignore')}...")
                cot_out.send(cot_xml)
                log(f"[cot] sent PLI from JSON uid={uid} lat={dat['lat']:.6f} lon={dat['lon']:.6f}")
            elif dat["type"] == "chat":
                uid = dat.get("uid") or f"GeoChat.{pkt_sender_uid(packet)}"  # Fallback to pkt if no "f"; but use dat["from"] if present
                if "from" in dat:
                    uid = f"GeoChat.{dat['from']}.{dat['to']}.{str(uuid.uuid4()).upper()}"  # Generate full
                typ = dat.get("t") or DEFAULT_CHAT_TYPE
                chat_info = {"from": dat["from"], "to": dat["to"], "message": dat["message"]}
                cot_xml = make_cot(
                    uid, typ, dat["lat"], dat["lon"], dat.get("alt", 0), dat["ts"],
                    chat=chat_info,
                    callsign=dat.get("callsign"),
                    group_name=(dat.get("group") or {}).get("name"),
                    group_role=(dat.get("group") or {}).get("role"),
                )
                log(f"[rx] Created CoT XML for CHAT: {cot_xml[:200].decode('utf-8', 'ignore')}...")
                cot_out.send(cot_xml)
                log(f"[cot] sent CHAT from JSON uid={uid} from={dat['from']} message={dat['message'][:20]}...")
    except Exception as e:
        log(f"[rx] Ignored packet due to processing error: {e}")


# ======================= TAK Ingress (UDP multicast) ===================
def cot_ingress_loop(mesh: Mesh):
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

# ======================= Main ================================
def main():
    if not is_local:
        # health thread
        threading.Thread(target=tcp_probe, daemon=True).start()

    # serial device pick
    dev = SERIAL_DEV if os.path.exists(SERIAL_DEV) else (sorted(glob.glob("/dev/ttyACM*")) + [None])[0]
    if not dev:
        log("[mesh] no serial device found; plug in RAK4631"); sys.exit(1)

    # connect radio
    global mesh
    mesh.connect()

    # wire pubsub handlers
    pub.subscribe(on_mesh_receive, "meshtastic.receive")
    log("[mesh] subscribed to pubsub topic 'meshtastic.receive'")

    # TAK server
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

# instantiate objects
mesh = Mesh(SERIAL_DEV if os.path.exists(SERIAL_DEV) else (sorted(glob.glob("/dev/ttyACM*")) + [None])[0])
tak_server = None
cot_out = None
udp_send_sock = None

if __name__ == "__main__":
    main()
