"""
sound_discovery_server.py -- PC's real-time sound discovery/mapping tool,
launched on demand from kyber_config_server.py's /open_mapper route (same
launch pattern Pi uses via start_sound_mapper()). Proxies sound playback
through kyber_core.py's own status server (port 5010) -- the brain keeps
the live BLE connection, this process never touches the droid directly,
same design as Pi's real play_sound_via_brain().

Rewritten with stdlib http.server instead of Pi's aiohttp, to match PC's
existing convention (kyber_config_server.py and kyber_core.py's status
server are both stdlib-only) rather than adding a new dependency.

Two real fixes vs Pi's actual code, per Kalvin's own call:
- /api/extend now actually exists -- the UI already calls it (the "Extend
  My Session" button), but Pi's server never registered that route at all.
- api_status() now actually returns timeout_remaining -- the UI already
  reads it to drive the countdown warning, but Pi's server never computed
  or returned it, so the warning modal likely never fired there either.
Both were apparently meant to force a periodic session restart (bounded
session length, extendable) -- likely a Pi memory-hygiene measure -- so
that intent is preserved; only the fact that neither half of it ever
actually worked before is what changed.

sound_discovery_ui.html (same directory) is unchanged from Pi's real file
-- it's pure front-end with no OS-specific logic, and it already calls
exactly the endpoints this server now correctly implements.

Run directly (normally launched by the Mainframe server, not by hand):
    python sound_discovery_server.py
"""

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import ENV_PATH, MAP_DIR

PORT = 5000
KYBER_CORE_STATUS_PORT = 5010  # must match kyber_core.py's own STATUS_PORT
SESSION_TIMEOUT_SECONDS = 600  # 10 minutes -- matches the UI's own TIMEOUT_TOTAL


def get_active_sound_profile() -> int:
    from dotenv import dotenv_values
    vals = dotenv_values(ENV_PATH) if os.path.exists(ENV_PATH) else {}
    return int(vals.get("MAPPER_SOUND_PROFILE", vals.get("ACTIVE_SOUND_PROFILE", "1")))


# R-series sound bank layout -- ported directly from Pi's real RUNIT_BANKS.
# Not verified against other chassis types (BB/C/A/BD); Pi's own mapper
# only ever supported R-series droids, so this is a known, pre-existing
# limitation carried over, not a new one introduced here.
RUNIT_BANKS = {
    1:  {"name": "General Use",         "sounds": 4},
    2:  {"name": "Droid Depot",         "sounds": 4},
    3:  {"name": "Resistance",          "sounds": 3},
    4:  {"name": "Unknown",             "sounds": 1},
    5:  {"name": "Droid Detector",      "sounds": 1},
    6:  {"name": "Dok Ondar's",         "sounds": 4},
    7:  {"name": "First Order",         "sounds": 5},
    8:  {"name": "Initial Activation",  "sounds": 1},
    9:  {"name": "Motor Sound",         "sounds": 1},
    11: {"name": "Blaster Accessory",   "sounds": 2},
    12: {"name": "Thruster Accessory",  "sounds": 2},
}

SOUND_LIST = []
for _bank_id, _info in RUNIT_BANKS.items():
    for _sound_id in range(1, _info["sounds"] + 1):
        SOUND_LIST.append({
            "bank_id": _bank_id,
            "sound_id": _sound_id,
            "bank_name": _info["name"],
            "label": f"{_info['name']} -- Sound {_sound_id}",
        })

current_index = 0
labels = {}
skipped = set()
session_deadline = time.time() + SESSION_TIMEOUT_SECONDS
_lock = threading.Lock()


def get_timeout_remaining() -> int:
    return max(0, int(session_deadline - time.time()))


def extend_session():
    global session_deadline
    with _lock:
        session_deadline = time.time() + SESSION_TIMEOUT_SECONDS


def sound_key(bank_id, sound_id):
    return f"{bank_id}_{sound_id}"


def get_save_path() -> str:
    sound_profile = get_active_sound_profile()
    os.makedirs(MAP_DIR, exist_ok=True)
    return os.path.join(MAP_DIR, f"sound_profile_{sound_profile}.json")


def read_sound_profile_name(slot: int) -> str:
    map_path = os.path.join(MAP_DIR, f"sound_profile_{slot}.json")
    if os.path.exists(map_path):
        try:
            with open(map_path) as f:
                data = json.load(f)
            return data.get("name", f"Sound Profile {slot}")
        except Exception:
            pass
    return f"Sound Profile {slot}"


def write_sound_profile_name(slot: int, name: str):
    map_path = os.path.join(MAP_DIR, f"sound_profile_{slot}.json")
    existing = {}
    if os.path.exists(map_path):
        try:
            with open(map_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing["name"] = name
    os.makedirs(MAP_DIR, exist_ok=True)
    with open(map_path, "w") as f:
        json.dump(existing, f, indent=2)


def load_progress():
    global labels, skipped, current_index
    save_path = get_save_path()
    if os.path.exists(save_path):
        with open(save_path) as f:
            data = json.load(f)
        if "sound_to_emotions" in data:
            labels = data["sound_to_emotions"]
        elif "labels" in data:
            labels = data["labels"]
        else:
            labels = {}
        skipped = set(data.get("skipped", []))
        current_index = 0  # always start at the beginning
        print(f"Loaded sound profile {get_active_sound_profile()}: "
              f"{len(labels)} labeled, {len(skipped)} skipped", flush=True)
    else:
        labels = {}
        skipped = set()
        current_index = 0
        print(f"Starting fresh -- sound profile {get_active_sound_profile()}", flush=True)


def save_progress():
    sound_profile = get_active_sound_profile()
    save_path = get_save_path()

    emotion_map = {}
    for key, emotion_list in labels.items():
        parts = key.split("_")
        if len(parts) != 2:
            continue
        bank_id, sound_id = int(parts[0]), int(parts[1])
        for emotion in emotion_list:
            emotion_map.setdefault(emotion, []).append({"bank_id": bank_id, "sound_id": sound_id})

    existing = {}
    if os.path.exists(save_path):
        try:
            with open(save_path) as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update({
        "sound_profile": sound_profile,
        "current_index": current_index,
        "emotion_to_sounds": emotion_map,
        "sound_to_emotions": labels,
        "skipped": list(skipped),
        "stats": {"total": len(SOUND_LIST), "labeled": len(labels), "skipped": len(skipped)},
    })

    with open(save_path, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"Saved sound profile {sound_profile} map to {save_path}", flush=True)


def play_sound_via_brain(bank_id: int, sound_id: int) -> bool:
    """Proxies to kyber_core.py's own status server -- the brain keeps the
    live BLE connection, this process never touches the droid directly."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{KYBER_CORE_STATUS_PORT}/play",
            data=json.dumps({"bank_id": bank_id, "sound_id": sound_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            result = json.loads(r.read())
            if not result.get("ok"):
                print(f"[WARN] Brain reported error: {result.get('reason', 'unknown')}", flush=True)
            return bool(result.get("ok"))
    except Exception as e:
        print(f"[WARN] Could not reach the brain: {e}", flush=True)
        return False


def set_brain_mapper_mode(active: bool):
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{KYBER_CORE_STATUS_PORT}/mode",
            data=json.dumps({"active": active}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


class MapperHandler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/":
            if getattr(sys, "frozen", False):
                # Bundled read-only asset -> PyInstaller extraction dir, not
                # the exe dir (that's reserved for writable data).
                base_dir = sys._MEIPASS
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            html_path = os.path.join(base_dir, "sound_discovery_ui.html")
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/status":
            s = SOUND_LIST[current_index] if current_index < len(SOUND_LIST) else None
            key = sound_key(s["bank_id"], s["sound_id"]) if s else None
            sound_profile = get_active_sound_profile()
            self._json({
                "current_index": current_index,
                "total": len(SOUND_LIST),
                "current": s,
                "current_key": key,
                "current_labels": labels.get(key, []),
                "labeled": len(labels),
                "skipped": len(skipped),
                "done": current_index >= len(SOUND_LIST),
                "sound_profile": sound_profile,
                "name": read_sound_profile_name(sound_profile),
                "timeout_remaining": get_timeout_remaining(),
            })
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        global current_index

        if self.path == "/api/play":
            if current_index < len(SOUND_LIST):
                s = SOUND_LIST[current_index]
                play_sound_via_brain(s["bank_id"], s["sound_id"])
                self._json({"ok": True, "playing": s["label"]})
            else:
                self._json({"ok": False, "reason": "no current sound"})
            return

        if self.path == "/api/next":
            if current_index < len(SOUND_LIST) - 1:
                current_index += 1
            self._json({"ok": True, "current_index": current_index})
            return

        if self.path == "/api/back":
            if current_index > 0:
                current_index -= 1
            self._json({"ok": True, "current_index": current_index})
            return

        if self.path == "/api/skip":
            s = SOUND_LIST[current_index]
            key = sound_key(s["bank_id"], s["sound_id"])
            skipped.add(key)
            save_progress()
            if current_index < len(SOUND_LIST) - 1:
                current_index += 1
            self._json({"ok": True, "skipped": key})
            return

        if self.path == "/api/label":
            data = self._read_json_body()
            s = SOUND_LIST[current_index]
            key = sound_key(s["bank_id"], s["sound_id"])
            emotion_list = data.get("emotions", [])
            if emotion_list:
                labels[key] = emotion_list
                skipped.discard(key)
                save_progress()
                if current_index < len(SOUND_LIST) - 1:
                    current_index += 1
                    next_s = SOUND_LIST[current_index]
                    play_sound_via_brain(next_s["bank_id"], next_s["sound_id"])
                self._json({"ok": True, "labeled": key, "emotions": emotion_list})
            else:
                self._json({"ok": False, "reason": "no emotions provided"})
            return

        if self.path == "/api/jump":
            data = self._read_json_body()
            idx = data.get("index", current_index)
            if isinstance(idx, int) and 0 <= idx < len(SOUND_LIST):
                current_index = idx
            self._json({"ok": True, "current_index": current_index})
            return

        if self.path == "/api/save":
            data = self._read_json_body()
            name = (data.get("name") or "").strip()
            sound_profile = get_active_sound_profile()
            if name:
                write_sound_profile_name(sound_profile, name)
            save_progress()
            saved_name = read_sound_profile_name(sound_profile)
            # No restart forced -- matches the no-restart-needed pattern
            # already established everywhere else on PC.
            self._json({
                "ok": True,
                "saved_to": f"sound_profile_{sound_profile}.json",
                "name": saved_name,
                "labeled": len(labels),
            })
            return

        if self.path == "/api/extend":
            extend_session()
            self._json({"ok": True, "timeout_remaining": get_timeout_remaining()})
            return

        if self.path == "/api/shutdown":
            # Cleanup happens HERE, synchronously, not via a signal handler
            # after the fact -- os.kill(pid, SIGTERM) on Windows actually
            # calls TerminateProcess() under the hood, bypassing any
            # registered Python signal handler entirely (same gotcha
            # documented in tray_shell.py's own shutdown logic). Relying on
            # a signal handler to save progress here would silently never
            # run on Windows.
            if labels:
                save_progress()
            set_brain_mapper_mode(False)
            print("\n[SHUTDOWN]: Sound mapper closed by user.", flush=True)
            self._json({"ok": True})
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    print("Checking brain status...", flush=True)
    brain_ready = False
    for attempt in range(10):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{KYBER_CORE_STATUS_PORT}/status", timeout=2) as r:
                data = json.loads(r.read())
                if data.get("connected"):
                    brain_ready = True
                    break
        except Exception:
            pass
        print(f"  Waiting for brain... ({attempt + 1}/10)", flush=True)
        time.sleep(2)

    if brain_ready:
        print("Brain connected -- droid ready", flush=True)
    else:
        print("[WARN] Could not reach the brain -- sounds may not play", flush=True)

    set_brain_mapper_mode(True)
    load_progress()
    print(f"Ready -- {len(SOUND_LIST)} sounds to explore", flush=True)

    server = ThreadingHTTPServer(("0.0.0.0", PORT), MapperHandler)
    print(f"\nDroid Sound Mapper -- {len(SOUND_LIST)} sounds")
    print(f"Open http://localhost:{PORT} in your browser\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if labels:
            save_progress()
        set_brain_mapper_mode(False)


if __name__ == "__main__":
    main()
