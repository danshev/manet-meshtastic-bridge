from datetime import datetime, timedelta, timezone

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