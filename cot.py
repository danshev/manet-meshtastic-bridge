import uuid
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element, SubElement, tostring

from utils import iso_z

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