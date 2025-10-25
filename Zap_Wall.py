#!/usr/bin/env python3
"""
ZapBoard CLI+GUI by Chanwut – Ultra Debug Edition, PyQt5 Wall
=============================================================
See every zap in your terminal *and* on a fullscreen scrolling comment wall!
- Esc to exit GUI.
- Color debug in terminal.
- All logic for Nostr zap, lnaddress, comment.
"""

import json, sys, threading, time, uuid, traceback
from datetime import datetime
from pathlib import Path

import websocket
from bolt11 import decode as decode_bolt11
from colorama import Fore, Style, init as color_init

from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtGui import QCursor

TOTAL_SAT = 0

# ------------------------- CONFIG -------------------------------
LNBITS_WS = "xxx"
#For example LNBITS_WS = wss://NODE_URL/api/v1/ws/INVOICE_READ_KEY

RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]
PROFILE_TIMEOUT = 4
VERBOSE_LNBITS_RAW = True
VERBOSE_RELAY = True
LOG_FILE = None
MAX_COMMENTS = 6
# ---------------------------------------------------------------
color_init()

# ---------- Logging (Console/Terminal) ----------
def log(msg: str, colour=Fore.GREEN):
    ts = datetime.now().strftime("%H:%M:%S")
    out = f"{Fore.CYAN}{ts}{Style.RESET_ALL} {colour}{msg}{Style.RESET_ALL}"
    print(out, flush=True)
    if LOG_FILE:
        LOG_FILE.write_text(out + "\n", append=True)

def debug(msg: str):
    log(f"[DEBUG] {msg}", Fore.YELLOW)

# ----------- PyQt5 Big Wall GUI -------------
class ZapWall(QWidget):
    def __init__(self):
        super().__init__()
        self.comments = []
        self.total_sats = 0
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('ZapBoard Live Wall')
        self.layout = QVBoxLayout(self)

        # (Optionally, add your QR or address label here...)

        # Add main comment labels
        self.labels = []
        for _ in range(MAX_COMMENTS):
            label = QLabel('')
            label.setAlignment(Qt.AlignCenter)
            label.setFont(QFont('Arial', 16, QFont.Bold))
            label.setWordWrap(True)                 # <-- Add this!
            label.setMaximumWidth(self.width())     # <-- Set maximum width
            self.layout.addWidget(label)
            self.labels.append(label)

        # ----- ADD THIS LABEL LAST -----
        self.total_label = QLabel(f"จำนวน Sat สะสม : {self.total_sats}", self)
        self.total_label.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.total_label.setFont(QFont('Arial', 20, QFont.Bold))
        self.total_label.setStyleSheet("color: #ffeb3b; margin-right: 30px; margin-bottom: 10px;")
        self.layout.addWidget(self.total_label)

        self.setStyleSheet("background-color: #111; color: #fff;")
        self.setCursor(QCursor(Qt.BlankCursor))
        
        #self.showFullScreen()
        self.setWindowFlags(Qt.FramelessWindowHint)   # <-- Hides top bar!
        self.resize(730, 590) # <-- Window size
        self.show()

    def add_comment(self, msg, sats=None):
        if len(self.comments) >= MAX_COMMENTS:
            self.comments.pop(0)
        self.comments.append(msg)
        for i, label in enumerate(self.labels):
            if i < len(self.comments):
                label.setText(self.comments[i])
            else:
                label.setText('')
        # Update total sats
        if sats is not None:
            self.total_sats += sats
            self.total_label.setText(f"จำนวน Sat สะสม : {self.total_sats:,}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

# ----------- Nostr Profile Lookup -----------
def fetch_profile_name(pubkey: str) -> str:
    """Fetch display_name / name / fallback pubkey from the first relay that answers."""
    sub_id = uuid.uuid4().hex[:8]
    filt = {"authors": [pubkey], "kinds": [0], "limit": 1}
    for relay in RELAYS:
        try:
            debug(f"Connecting to relay {relay} …")
            ws = websocket.create_connection(relay, timeout=PROFILE_TIMEOUT)
            if VERBOSE_RELAY:
                debug(f"→ SEND   ['REQ', '{sub_id}', {filt}]")
            ws.send(json.dumps(["REQ", sub_id, filt]))
            ws.settimeout(PROFILE_TIMEOUT)
            while True:
                frame = ws.recv()
                if VERBOSE_RELAY:
                    debug(f"← RECV   {frame[:120]}…")
                msg = json.loads(frame)
                if msg[0] == "EVENT" and msg[1] == sub_id:
                    meta = json.loads(msg[2]["content"])
                    name = (
                        meta.get("display_name")
                        or meta.get("name")
                        or meta.get("username")
                    )
                    ws.close()
                    if name:
                        debug(f"Profile found: {name}")
                        return name
                    break
                if msg[0] == "EOSE":
                    debug("Reached EOSE – no profile on this relay")
                    break
            ws.close()
        except Exception as e:
            debug(f"Relay {relay} error: {e}")
    return pubkey[:12] + "…"  # fallback

# ----------- Main LNbits/Wall Handler -----------
def handle_zap(sats, zap_content, name, gui=None):
    global TOTAL_SAT
    msg = f"⚡ {sats} sats from {name}\n{zap_content}"
    TOTAL_SAT += sats
    log(msg, Fore.MAGENTA)
    if gui:
        #gui.add_comment(msg)
        gui.add_comment(msg, sats=sats)  # Pass sats to update total

def on_message_handler(gui, raw: str):
    if VERBOSE_LNBITS_RAW:
        debug(f"Raw LNbits msg: {raw[:300]}…")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        debug(f"JSON error: {e}")
        return

    # Old/new LNbits format
    if "payment_hash" in data:
        invoice = data
    elif "payment" in data and "payment_hash" in data["payment"]:
        invoice = data["payment"]
    else:
        debug("Message has no usable payment_hash → skipping")
        return

    if not invoice.get("paid", False) and not invoice.get("status") == "success":
        debug("Invoice not marked as paid → skipping")
        return

    try:
        bolt = decode_bolt11(invoice["bolt11"])
        sats = bolt.amount_msat // 1000
        debug(f"Invoice decoded: {sats} sats")
    except Exception as e:
        debug(f"bolt11 decode failed: {e}\n{traceback.format_exc(limit=2)}")
        return

    pubkey = None
    zap_content = None
    is_nostr_zap = False
    name = "someone"

    # Detect Nostr zap
    if "extra" in invoice and "nostr" in invoice["extra"]:
        try:
            nostr_json = invoice["extra"]["nostr"]
            zap_req = json.loads(nostr_json)
            pubkey = zap_req.get("pubkey")
            zap_content = zap_req.get("content", "")
            debug(f"Zap‑request pubkey found: {pubkey}, content: {zap_content}")
            is_nostr_zap = True
        except Exception as e:
            debug(f"Zap‑request decode error: {e}")

    if is_nostr_zap and pubkey:
        name = fetch_profile_name(pubkey)
    else:
        # Not a nostr zap, try LN address
        zap_content = ""
        if "extra" in invoice and "comment" in invoice["extra"]:
            cmt = invoice["extra"]["comment"]
            if isinstance(cmt, list) and cmt:
                zap_content = cmt[0]
            elif isinstance(cmt, str):
                zap_content = cmt
        if not zap_content:
            zap_content = invoice.get("memo", "") or invoice.get("comment", "") or bolt.description or ""
        name = "someone"

    # Fallback for zap_content if Nostr zap
    if is_nostr_zap and not zap_content:
        zap_content = "⚡️"

    handle_zap(sats, zap_content, name, gui=gui)

# ----------- WebSocket Logic -----------
def on_ws_message(ws, msg):
    on_message_handler(ws.gui, msg)

def run_websocket(gui=None):
    ws = websocket.WebSocketApp(
        LNBITS_WS,
        on_open=lambda ws: debug("LNbits WebSocket connection opened ✓"),
        on_message=lambda ws, msg: on_ws_message(ws, msg),
        on_error=lambda ws, err: debug(f"LNbits WebSocket error: {err}"),
        on_close=lambda ws, status, reason: debug(f"LNbits WebSocket closed (status={status}, reason={reason})")
    )
    ws.gui = gui
    ws.run_forever(ping_interval=20, ping_timeout=8)

# ----------- Main Entrypoint -----------
def main():
    # Launch GUI wall
    app = QApplication(sys.argv)
    gui = ZapWall()
    t = threading.Thread(target=run_websocket, args=(gui,), daemon=True)
    t.start()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()

