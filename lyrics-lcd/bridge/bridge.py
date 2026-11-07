"""
Lyrics LCD - Python bridge
==========================
This program sits in the middle:

    Chrome extension  <--HTTP-->  bridge.py  <--USB serial-->  Arduino

What it does
  1. Finds your Arduino on a USB port and talks to it (pyserial).
  2. Runs a tiny web server on http://127.0.0.1:8765 (Flask). The extension
     sends the current video id + playback time to it about 3 times per second.
  3. When a new video starts, downloads its captions (youtube-transcript-api)
     and chops them into "pages" of 2 lines x 16 characters.
  4. Sends the page that matches the current time to the LCD.
  5. When you press an Arduino button it queues a command ("PLAY" / "NEXT")
     that the extension picks up on its next request.

Run:   python bridge.py
       python bridge.py --port COM3          (or /dev/ttyACM0)
       python bridge.py --offset 0.5         (show lyrics 0.5 s earlier)
"""

import argparse
import bisect
import logging
import re
import textwrap
import threading
import time
import unicodedata

import serial                       # from the "pyserial" package
import serial.tools.list_ports
from flask import Flask, jsonify, request
from youtube_transcript_api import (
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

# ============================== SETTINGS =================================
BAUD_RATE = 115200          # must match Serial.begin() in main.cpp
LCD_COLS = 16               # characters per LCD row
LCD_ROWS = 2
SERVER_HOST = "127.0.0.1"   # only this computer can reach the server
SERVER_PORT = 8765
LANGUAGES = ["en", "en-US", "en-GB"]   # preferred caption languages, in order
TIME_OFFSET = 0.0           # seconds. Positive = lyrics appear EARLIER, negative = LATER
IDLE_AFTER = 5.0            # seconds without the extension before showing "waiting"
# =========================================================================

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # hide per-request spam

state_lock = threading.Lock()

# Everything the bridge remembers about the current video (like a JS object).
state = {
    "video_id": None,
    "title": "",
    "status": "idle",       # idle | loading | ready | no_captions | blocked | error
    "starts": [],           # start time (seconds) of every page, sorted
    "pages": [],            # [{"start":..., "end":..., "l1":..., "l2":...}, ...]
    "last_sent": None,      # last (line1, line2) sent, so we only send changes
    "last_sync": 0.0,       # when the extension last contacted us
    "idle_shown": False,
}

pending_commands = []       # e.g. ["PLAY", "NEXT"], filled by Arduino button presses

arduino = {"ser": None, "port": None}   # the open serial connection
arduino_lock = threading.Lock()
forced_port = None                      # set by --port


# =========================== TEXT HELPERS ================================
_REPLACEMENTS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", "♪": "", "♫": "",
}


def clean_text(text):
    """The LCD only understands plain ASCII, so convert or remove anything else."""
    text = re.sub(r"\[[^\]]*\]", " ", text)             # sound tags: [Music], [Applause]
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    text = text.replace("\n", " ").replace("|", " ")    # '|' is our message separator
    text = unicodedata.normalize("NFKD", text)          # e.g. "e with accent" -> "e" + accent
    text = text.encode("ascii", "ignore").decode("ascii")  # drop what is left
    return " ".join(text.split())                       # squeeze repeated spaces


def make_pages(snippets):
    """
    Turn caption snippets into LCD pages.

    snippets: list of (start_seconds, duration_seconds, text)
    A long caption becomes several pages, each shown for an equal slice of
    the caption's time.
    """
    pages = []
    for i, (start, duration, text) in enumerate(snippets):
        text = clean_text(text)
        if not text:
            continue

        # Captions often overlap the next one, so end at whichever comes first
        end = start + duration
        if i + 1 < len(snippets):
            end = min(end, snippets[i + 1][0])
        if end <= start:
            end = start + 1.0

        lines = textwrap.wrap(text, width=LCD_COLS)          # wrap at 16 characters
        groups = [lines[j:j + LCD_ROWS] for j in range(0, len(lines), LCD_ROWS)]
        slice_len = (end - start) / len(groups)

        for g, group in enumerate(groups):
            while len(group) < LCD_ROWS:
                group.append("")
            pages.append({
                "start": start + g * slice_len,
                "end": start + (g + 1) * slice_len,
                "l1": group[0],
                "l2": group[1],
            })

    pages.sort(key=lambda p: p["start"])
    return pages


def find_page(pages, starts, t):
    """Return the page that should be on screen at time t, or None (a gap)."""
    idx = bisect.bisect_right(starts, t) - 1    # last page that started <= t
    if idx < 0:
        return None
    page = pages[idx]
    if t < page["end"]:
        return page
    return None


# ============================ ARDUINO SIDE ===============================
def find_arduino_port():
    """Look through the USB serial ports for something that looks like an Arduino."""
    if forced_port:
        return forced_port
    for p in serial.tools.list_ports.comports():
        info = f"{p.description} {p.manufacturer}".lower()
        known_vid = p.vid in (0x2341, 0x2A03, 0x1A86, 0x0403)  # Arduino, CH340, FTDI
        if known_vid or "arduino" in info or "ch340" in info or "usb serial" in info:
            return p.device
    return None


def drop_arduino():
    """Close and forget the serial connection (the worker thread will reconnect)."""
    with arduino_lock:
        ser = arduino["ser"]
        arduino["ser"] = None
        arduino["port"] = None
    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass


def send_to_lcd(line1, line2=""):
    """Send one 'LCD|line1|line2' message. Does nothing if no Arduino is connected."""
    line1 = clean_text(line1)[:LCD_COLS]
    line2 = clean_text(line2)[:LCD_COLS]
    key = (line1, line2)
    if state["last_sent"] == key:
        return                                   # the screen already shows this
    with arduino_lock:
        ser = arduino["ser"]
    if ser is None:
        return
    try:
        ser.write(f"LCD|{line1}|{line2}\n".encode("ascii"))
        state["last_sent"] = key
    except (serial.SerialException, OSError):
        drop_arduino()


def arduino_worker():
    """Background thread: keep the connection alive, read button presses, show idle text."""
    while True:
        ser = arduino["ser"]
        if ser is None:
            port = find_arduino_port()
            if port:
                try:
                    new_ser = serial.Serial(port, BAUD_RATE, timeout=0.5)
                    time.sleep(2)   # the Uno restarts when the port opens; wait for it
                    with arduino_lock:
                        arduino["ser"] = new_ser
                        arduino["port"] = port
                    state["last_sent"] = None    # force a fresh screen update
                    print(f"[arduino] connected on {port}")
                except (serial.SerialException, OSError) as e:
                    print(f"[arduino] could not open {port}: {e}")
            time.sleep(2)
            continue

        try:
            line = ser.readline().decode("ascii", errors="ignore").strip()
        except (serial.SerialException, OSError):
            print("[arduino] disconnected")
            drop_arduino()
            continue

        if line == "BTN:PLAY":
            pending_commands.append("PLAY")
            print("[button] play/pause")
        elif line == "BTN:NEXT":
            pending_commands.append("NEXT")
            print("[button] next")

        # If the extension has gone quiet, tell the user on the LCD.
        if time.time() - state["last_sync"] > IDLE_AFTER and not state["idle_shown"]:
            state["idle_shown"] = True
            send_to_lcd("Waiting for", "YouTube tab...")


# ========================= TRANSCRIPT LOADING ============================
def fetch_snippets(video_id):
    """Download captions. Returns a list of (start, duration, text)."""
    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=LANGUAGES)
    except NoTranscriptFound:
        # None in our preferred languages: use whichever caption track exists
        first = next(iter(api.list(video_id)))
        fetched = first.fetch()
    return [(s.start, s.duration, s.text) for s in fetched]


def load_transcript(video_id):
    """Runs in its own thread so the server stays responsive while downloading."""
    print(f"[captions] loading {video_id} ...")
    pages = []
    try:
        pages = make_pages(fetch_snippets(video_id))
        status = "ready" if pages else "no_captions"
    except (NoTranscriptFound, TranscriptsDisabled, StopIteration):
        status = "no_captions"
    except (IpBlocked, RequestBlocked):
        status = "blocked"
        print("[captions] YouTube blocked this request (VPN or cloud IP?)")
    except Exception as e:  # any other problem: report it and carry on
        status = "error"
        print(f"[captions] failed: {type(e).__name__}: {str(e)[:200]}")

    with state_lock:
        if state["video_id"] != video_id:
            return                               # the user already moved to another video
        state["pages"] = pages
        state["starts"] = [p["start"] for p in pages]
        state["status"] = status
        title = state["title"]
    print(f"[captions] {status} ({len(pages)} pages)")

    messages = {
        "no_captions": "No captions",
        "blocked": "YouTube blocked",
        "error": "Caption error",
    }
    if status in messages:
        send_to_lcd(title[:LCD_COLS], messages[status])


# ============================== WEB SERVER ===============================
@app.route("/status")
def status():
    """Used by the extension popup to show what is connected."""
    return jsonify(
        bridge=True,
        arduino=arduino["ser"] is not None,
        port=arduino["port"],
        video=state["video_id"],
        captions=state["status"],
        offset=TIME_OFFSET,
    )


@app.route("/sync", methods=["POST"])
def sync():
    """The extension calls this about 3x per second with the current video state."""
    data = request.get_json(force=True, silent=True) or {}
    video_id = data.get("videoId")
    title = data.get("title", "") or ""
    is_ad = bool(data.get("ad", False))
    try:
        t = float(data.get("time", 0) or 0)
    except (TypeError, ValueError):
        t = 0.0

    state["last_sync"] = time.time()
    state["idle_shown"] = False

    start_loading = False
    with state_lock:
        if video_id != state["video_id"]:
            # A different video (or none): reset, and start loading if there is one
            state.update(video_id=video_id, title=title, status="idle",
                         pages=[], starts=[], last_sent=None)
            if video_id:
                state["status"] = "loading"
                start_loading = True
        pages, starts, current_status = state["pages"], state["starts"], state["status"]

    if video_id is None:
        send_to_lcd("Open a video", "on YouTube")
    elif start_loading:
        send_to_lcd(title[:LCD_COLS], "Loading...")
        threading.Thread(target=load_transcript, args=(video_id,), daemon=True).start()
    elif is_ad:
        send_to_lcd("Ad playing...", "")
    elif current_status == "ready":
        page = find_page(pages, starts, t + TIME_OFFSET)
        if page:
            send_to_lcd(page["l1"], page["l2"])
        else:
            send_to_lcd("", "~ music ~")           # a gap with no captions

    # Hand over any button commands, then clear the queue
    commands = pending_commands[:]
    del pending_commands[:len(commands)]
    return jsonify(commands=commands, arduino=arduino["ser"] is not None)


# ================================ MAIN ===================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lyrics LCD bridge")
    parser.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyACM0")
    parser.add_argument("--offset", type=float, default=TIME_OFFSET,
                        help="seconds; positive shows lyrics earlier")
    args = parser.parse_args()
    forced_port = args.port
    TIME_OFFSET = args.offset

    threading.Thread(target=arduino_worker, daemon=True).start()
    print(f"Bridge running at http://{SERVER_HOST}:{SERVER_PORT}  (Ctrl+C to stop)")
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)
