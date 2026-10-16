"""Background threads that receive analyzer results while the app runs.

H33: listens on the server address/port set on the H33 (Setup > Ethernet Port
Setup), answers every message with an HL7 ACK, saves the raw bytes, then
stores the parsed run. 100plusV: polls result.xlsx every 30 seconds over SSH.
Each keeps a status the Instruments page shows.
"""

import json
import re
import socket
import threading
import time
from datetime import datetime

import db

from . import h33_hl7, network, store, unics100

VT, FS, CR = b"\x0b", b"\x1c", b"\x0d"
POLL_SECONDS = 30

status = {
    "H33": {"state": "stopped", "detail": "", "peer": "", "last_message": "", "received": 0},
    "U100": {"state": "stopped", "detail": "", "last_poll": "", "last_new": "", "received": 0,
             "next_patient_id": "", "unit_mac": ""},
}
_started = False
_lock = threading.Lock()
_wake = {"H33": threading.Event(), "U100": threading.Event()}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _config(code):
    with db.connect() as conn:
        row = store.instrument(conn, code)
        return dict(row) if row else None


def _set(code, **values):
    status[code].update(values)


def _hl7_ack(message):
    msh = next((s for s in h33_hl7.segments(message) if s[0] == "MSH"), [])
    get = lambda i: msh[i] if len(msh) > i else ""
    trigger = get(8).split("^")[1] if "^" in get(8) else "R01"
    ack = "\r".join([
        "|".join(["MSH", "^~\\&", "LIS", "", get(2), get(3), datetime.now().strftime("%Y%m%d%H%M%S"),
                  "", "ACK^{}".format(trigger), get(9), get(10) or "P", get(11) or "2.3.1"]),
        "MSA|AA|{}".format(get(9)),
    ]) + "\r"
    return VT + ack.encode("utf-8") + FS + CR


# ---------------------------------------------------------------- H33

def _h33_session(conn_sock, peer):
    raw_path = store.data_dir() / "raw" / "H33-{}.bin".format(datetime.now().strftime("%Y%m%d"))
    _set("H33", state="connected", peer=peer, detail="H33 connected from {}".format(peer.split(":")[0]))
    buffer = b""
    conn_sock.settimeout(None)
    with open(raw_path, "ab") as raw:
        while True:
            data = conn_sock.recv(65536)
            if not data:
                break
            raw.write(data)
            raw.flush()
            buffer += data
            while (start := buffer.find(VT)) >= 0 and (end := buffer.find(FS + CR, start)) >= 0:
                message = buffer[start + 1:end].decode("utf-8", "replace")
                buffer = buffer[end + 2:]
                try:
                    if store.ingest_h33(message, raw_path.name):
                        status["H33"]["received"] += 1
                    _set("H33", last_message=_now())
                except Exception as exc:  # the raw bytes are kept; keep receiving
                    _set("H33", detail="Could not store a message: {}".format(exc))
                conn_sock.sendall(_hl7_ack(message))


def _h33_loop():
    while True:
        cfg = _config("H33")
        if not cfg or not cfg["enabled"]:
            _set("H33", state="stopped", detail="Turned off in Instruments settings", peer="")
            _wake["H33"].wait(10)
            _wake["H33"].clear()
            continue
        host, port = cfg["host"], int(cfg["port"])
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((host, port))
        except OSError as exc:
            server.close()
            holder = network.holder_of(host)
            reason = ("{} is not on any adapter of this computer. Add it in Instruments settings.".format(host)
                      if not holder else "Cannot listen on {}:{} ({})".format(host, port, exc))
            _set("H33", state="error", detail=reason, peer="")
            _wake["H33"].wait(10)
            _wake["H33"].clear()
            continue
        server.listen(1)
        server.settimeout(2)
        _set("H33", state="waiting", detail="Waiting for the H33 on {}:{}".format(host, port), peer="")
        try:
            while True:
                if _wake["H33"].is_set():  # settings changed: rebind
                    _wake["H33"].clear()
                    break
                try:
                    conn_sock, (ip, peer_port) = server.accept()
                except socket.timeout:
                    continue
                try:
                    _h33_session(conn_sock, "{}:{}".format(ip, peer_port))
                except OSError as exc:
                    _set("H33", detail="Connection dropped: {}".format(exc))
                finally:
                    conn_sock.close()
                _set("H33", state="waiting", peer="", detail="Waiting for the H33 on {}:{}".format(host, port))
        finally:
            server.close()


# ---------------------------------------------------------------- 100plusV

def _u100_loop():
    known = ""
    while True:
        cfg = _config("U100")
        if not cfg or not cfg["enabled"]:
            _set("U100", state="stopped", detail="Turned off in Instruments settings")
        else:
            try:
                data = unics100.fetch(cfg["host"], cfg["username"], unics100.get_password(cfg["username"]),
                                      store.data_dir(), known)
                _set("U100", state="connected", last_poll=_now(), next_patient_id=data["next_patient_id"],
                     unit_mac=data["unit_mac"], detail="Reading result.xlsx every {} s".format(POLL_SECONDS))
                if data["rows"] is not None:
                    raw_path = store.data_dir() / "raw" / "U100-{}.jsonl".format(datetime.now().strftime("%Y%m%d"))
                    with open(raw_path, "a") as raw:
                        raw.write(json.dumps({"read_at": _now(), **data}) + "\n")
                    new = store.ingest_u100(data["rows"], data["ranges"] or {}, raw_path.name)
                    if new:
                        status["U100"]["received"] += len(new)
                        _set("U100", last_new=_now())
                    known = data["mtime"]
                elif data.get("error"):
                    _set("U100", detail="Analyzer busy, retrying: {}".format(data["error"]))
            except unics100.AnalyzerError as exc:
                _set("U100", state="error", detail=str(exc), last_poll=_now())
            except Exception as exc:
                _set("U100", state="error", detail="Unexpected: {}".format(exc), last_poll=_now())
        _wake["U100"].wait(POLL_SECONDS)
        _wake["U100"].clear()


# ---------------------------------------------------------------- control

def start_all():
    """Start both workers once per process."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    for target in (_h33_loop, _u100_loop):
        threading.Thread(target=target, daemon=True).start()


def wake(code):
    """Apply new settings now (H33 rebinds, 100plusV polls)."""
    _wake[code].set()


def running():
    return _started
