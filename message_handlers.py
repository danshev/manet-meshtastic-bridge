import json
import time
import uuid

from config import DEFAULT_UID, DEFAULT_TYPE, DEFAULT_CHAT_TYPE
from utils import log
from cot import make_cot

def pkt_sender_uid(pkt):
    frm = pkt.get("from") or pkt.get("fromId") or pkt.get("fromIdShort")
    return (DEFAULT_UID.replace("{from}", str(frm))) if frm else "MESH-UNKNOWN"

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

def on_mesh_receive(packet, interface):
    log("[rx] Received Meshtastic packet via pubsub")
    try:
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