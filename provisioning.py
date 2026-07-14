"""
provisioning.py -- KYBER PC's first-run "Core Upgrade" orchestrator.

This is the engine behind the /setup/provision screen. On first run (before the
onboarding wizard) it fetches everything heavy that the small installer left
out, into a self-contained `runtime\\` folder next to the app, and reports live
per-component status the page polls:

  * Logic Cores            -> Ollama runtime  (download + unzip the portable pkg)
  * Language Databanks     -> Qwen3 model     (ollama pull, with live % from the
                                               pull stream)
  * Audio Decryption
    Protocols              -> faster-whisper "small" (HuggingFace fetch)

Design notes:
  - Everything lives under PROJECT_DIR\\runtime (see config.PROJECT_DIR, which is
    the exe's folder once frozen). Nothing is scattered into the Windows user
    profile, so an uninstall that removes the app folder removes all of it.
  - KYBER supervises Ollama itself: we start the bundled `ollama serve` (pointed
    at our own model store via OLLAMA_MODELS) rather than assuming a system-wide
    install. configure_env() + ensure_ollama_running() are reused by tray_shell
    for normal (post-onboarding) operation too.
  - Idempotent: every step checks whether its work is already done and skips it,
    so a retry after a dropped connection resumes instead of restarting.
  - Runs in a background thread inside the Mainframe (kyber_config_server)
    process. status() is a plain dict the /setup/provision_status route returns.

The Ollama model is tier-driven via config.active_ollama_model() (shared with
kyber_core.py so the two can't drift). Whisper stays "small" on every tier (the
repo below). The tier is auto-detected once on first run (config.ensure_tier).
"""

import glob
import json
import os
import subprocess
import sys
import threading
import time
import zipfile

import requests

from config import PROJECT_DIR, update_env_values, active_ollama_model, ensure_tier

# ---------------------------------------------------------------------------
# What we install. The Ollama MODEL is tier-driven -- it comes from
# config.active_ollama_model() (4B on capable PCs, 1.5B on weak ones), so there
# is no hardcoded tag here to fall out of sync with kyber_core.py.
# ---------------------------------------------------------------------------

WHISPER_REPO = "Systran/faster-whisper-small"      # faster-whisper "small" (all tiers)
OLLAMA_HOST = "127.0.0.1:11434"                    # == kyber_core's Ollama host

# Pinned for reproducible beta installs -- every tester gets this exact runtime.
# Confirmed working on RandallPC (ollama.exe --version -> 0.31.2). Set to
# "latest" to instead resolve the newest Windows build from GitHub at run time.
OLLAMA_VERSION = "0.31.2"
OLLAMA_ZIP_ASSET = "ollama-windows-amd64.zip"

# ---------------------------------------------------------------------------
# Where it all lives (self-contained, next to the exe)
# ---------------------------------------------------------------------------

RUNTIME_DIR = os.path.join(PROJECT_DIR, "runtime")
OLLAMA_DIR = os.path.join(RUNTIME_DIR, "ollama")            # extracted runtime
OLLAMA_MODELS_DIR = os.path.join(RUNTIME_DIR, "models", "ollama")
WHISPER_DIR = os.path.join(RUNTIME_DIR, "models", "whisper")  # HF cache home

# Windows: don't pop console windows for the ollama subprocess.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Overall-progress weights (roughly by download size: ~1.8GB / ~2.5GB / ~0.46GB).
_WEIGHTS = {"logic": 0.38, "lang": 0.52, "audio": 0.10}


def configure_env():
    """Point Ollama and HuggingFace at our self-contained runtime folder, and put
    the bundled ollama.exe on PATH. Must run before anything talks to either.
    Reused by tray_shell so kyber_core's own whisper/Ollama use the same store."""
    os.makedirs(OLLAMA_MODELS_DIR, exist_ok=True)
    os.makedirs(WHISPER_DIR, exist_ok=True)
    os.environ["OLLAMA_MODELS"] = OLLAMA_MODELS_DIR
    os.environ["OLLAMA_HOST"] = OLLAMA_HOST
    os.environ["HF_HOME"] = WHISPER_DIR
    os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(WHISPER_DIR, "hub")
    # let faster-whisper/HF work fully offline once files are present, and skip
    # the tqdm progress bar entirely (it writes to stderr, which is None in a
    # windowed frozen build -- the "'NoneType' has no attribute 'write'" crash)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    exe_dir = os.path.dirname(find_ollama_exe() or os.path.join(OLLAMA_DIR, "ollama.exe"))
    if exe_dir and exe_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")


def find_ollama_exe():
    """Locate ollama.exe inside the extracted runtime (root or a subfolder)."""
    direct = os.path.join(OLLAMA_DIR, "ollama.exe")
    if os.path.exists(direct):
        return direct
    hits = glob.glob(os.path.join(OLLAMA_DIR, "**", "ollama.exe"), recursive=True)
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Live status (what the page polls)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_thread = None
_state = {
    "logic": {"state": "queued", "pct": 0, "activity": "Waiting…"},
    "lang":  {"state": "queued", "pct": 0, "activity": "Waiting…"},
    "audio": {"state": "queued", "pct": 0, "activity": "Waiting…"},
    "error": None,
}


def _set(key, state=None, pct=None, activity=None):
    with _lock:
        c = _state[key]
        if state is not None:
            c["state"] = state
        if pct is not None:
            c["pct"] = max(0, min(100, int(pct)))
        if activity is not None:
            c["activity"] = activity


def status():
    """Snapshot the page reads via GET /setup/provision_status."""
    with _lock:
        comps = []
        overall = 0.0
        for key in ("logic", "lang", "audio"):
            c = _state[key]
            comps.append({"key": key, "state": c["state"], "pct": c["pct"], "activity": c["activity"]})
            overall += _WEIGHTS[key] * (100 if c["state"] == "done" else c["pct"])
        done = all(_state[k]["state"] == "done" for k in ("logic", "lang", "audio"))
        return {
            "overall": 100 if done else int(overall),
            "done": done,
            "error": _state["error"],
            "components": comps,
        }


# ---------------------------------------------------------------------------
# Ollama runtime lifecycle (also used by tray_shell for normal operation)
# ---------------------------------------------------------------------------

_ollama_proc = None


def _ollama_up():
    try:
        r = requests.get(f"http://{OLLAMA_HOST}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def ensure_ollama_running(timeout=40):
    """Start the bundled `ollama serve` if it isn't already answering, and wait
    until its API is reachable. No-op if something is already serving on the
    port. Returns True once reachable."""
    global _ollama_proc
    if _ollama_up():
        return True
    exe = find_ollama_exe()
    if not exe:
        raise RuntimeError("Ollama runtime not installed yet")
    configure_env()
    _ollama_proc = subprocess.Popen(
        [exe, "serve"],
        cwd=os.path.dirname(exe),
        env=os.environ.copy(),
        creationflags=_CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ollama_up():
            return True
        time.sleep(1)
    raise RuntimeError("Ollama server did not come up in time")


def stop_ollama():
    """Stop the ollama server we started (called by tray_shell on exit).

    Kills the whole process TREE, not just ollama.exe: loading a model spawns a
    llama-server.exe child that holds GPU memory and locks the runtime DLLs, and
    a plain terminate() on Windows leaves that child orphaned (which then blocks
    the next rebuild / data wipe). taskkill /T takes the child down too."""
    global _ollama_proc
    if _ollama_proc is not None and _ollama_proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(_ollama_proc.pid)],
                    creationflags=_CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                _ollama_proc.terminate()
        except Exception:
            pass
    _ollama_proc = None


# ---------------------------------------------------------------------------
# Step 1 -- Logic Cores: download + unzip the Ollama runtime
# ---------------------------------------------------------------------------

def _resolve_ollama_zip_url():
    if OLLAMA_VERSION.lower() != "latest":
        return (f"https://github.com/ollama/ollama/releases/download/"
                f"v{OLLAMA_VERSION}/{OLLAMA_ZIP_ASSET}")
    # resolve the newest Windows build's asset URL from the GitHub API
    r = requests.get("https://api.github.com/repos/ollama/ollama/releases/latest", timeout=20)
    r.raise_for_status()
    for asset in r.json().get("assets", []):
        if asset.get("name") == OLLAMA_ZIP_ASSET:
            return asset["browser_download_url"]
    raise RuntimeError(f"Could not find {OLLAMA_ZIP_ASSET} in the latest Ollama release")


def _step_logic():
    if find_ollama_exe():
        _set("logic", state="done", pct=100, activity="Complete")
        return

    _set("logic", state="going", pct=0, activity="Locating runtime…")
    os.makedirs(OLLAMA_DIR, exist_ok=True)
    url = _resolve_ollama_zip_url()
    zip_path = os.path.join(RUNTIME_DIR, "_ollama_download.zip")

    # download (this is the ~1.8 GB bulk -> drives the % for this row)
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        got = 0
        _set("logic", activity="Fetching runtime…")
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 512):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if total:
                    _set("logic", pct=int(got / total * 95))  # leave 5% for unzip

    # extract (fast; shows real filenames as activity)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for i, name in enumerate(names):
            z.extract(name, OLLAMA_DIR)
            leaf = os.path.basename(name) or name
            if leaf.lower().endswith((".dll", ".exe")):
                _set("logic", activity=f"Extracting {leaf}")
            _set("logic", pct=95 + int((i + 1) / max(1, len(names)) * 5))

    try:
        os.remove(zip_path)
    except OSError:
        pass

    if not find_ollama_exe():
        raise RuntimeError("ollama.exe not found after extraction")
    _set("logic", state="done", pct=100, activity="Complete")


# ---------------------------------------------------------------------------
# Step 2 -- Language Databanks: ollama pull (with live progress)
# ---------------------------------------------------------------------------

# Friendlier, size-free phrasing for Ollama's raw pull statuses.
def _pull_activity(st, digest, pct):
    s = (st or "").lower()
    if "writing" in s:
        return "Writing model data package"
    if "manifest" in s:
        return "Pulling model manifest"
    if s.startswith("pulling") and digest:
        return f"Pulling {digest.split(':')[-1][:12]}… {pct}%"
    if "verif" in s:
        return "Verifying manifest"
    if "success" in s:
        return "Complete"
    return st or "Pulling model layer"


def _model_present():
    try:
        model = active_ollama_model()
        r = requests.get(f"http://{OLLAMA_HOST}/api/tags", timeout=5)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        return any(n == model or n.startswith(model) for n in names)
    except Exception:
        return False


def _step_lang():
    _set("lang", state="going", pct=0, activity="Starting brain server…")
    ensure_ollama_running()

    if _model_present():
        _set("lang", state="done", pct=100, activity="Complete")
        return

    _set("lang", activity="Pulling model manifest")
    with requests.post(
        f"http://{OLLAMA_HOST}/api/pull",
        json={"model": active_ollama_model(), "stream": True},
        stream=True,
        timeout=None,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            st = msg.get("status", "")
            total = msg.get("total") or 0
            completed = msg.get("completed") or 0
            pct = int(completed / total * 100) if total else None
            _set("lang", pct=pct, activity=_pull_activity(st, msg.get("digest"), pct or 0))
            if st == "success":
                break

    if not _model_present():
        raise RuntimeError("Model pull finished but the model is not present")
    _set("lang", state="done", pct=100, activity="Complete")


# ---------------------------------------------------------------------------
# Step 3 -- Audio Decryption Protocols: faster-whisper "small" from HuggingFace
# ---------------------------------------------------------------------------

def _step_audio():
    _set("audio", state="going", pct=0, activity="Preparing…")
    # hf import is lazy so importing this module stays cheap for other processes
    from huggingface_hub import hf_hub_download, list_repo_files

    files = list_repo_files(WHISPER_REPO)
    # download the big weight file last so the row's % climbs steadily
    files = sorted(files, key=lambda f: f.endswith(".bin"))
    n = len(files) or 1
    for i, fname in enumerate(files):
        _set("audio", activity=f"Fetching {os.path.basename(fname)}", pct=int(i / n * 100))
        hf_hub_download(repo_id=WHISPER_REPO, filename=fname)
        _set("audio", pct=int((i + 1) / n * 100))
    _set("audio", state="done", pct=100, activity="Complete")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_STEPS = [("logic", _step_logic), ("lang", _step_lang), ("audio", _step_audio)]


def _run():
    configure_env()
    ensure_tier()   # silent hardware detection -> pins KYBER_TIER on first run,
                    # so the pull below fetches the right-sized model
    with _lock:
        _state["error"] = None
    try:
        for key, fn in _STEPS:
            try:
                fn()
            except Exception as e:
                import traceback
                print(f"[PROVISION] {key} failed: {e}", flush=True)
                traceback.print_exc()
                _set(key, state="error", activity=str(e))
                with _lock:
                    _state["error"] = f"{key}: {e}"
                return
        update_env_values({"PROVISIONING_COMPLETE": "1"})
    except Exception as e:  # pragma: no cover -- last-ditch guard
        with _lock:
            _state["error"] = str(e)


def start():
    """Kick off provisioning in the background (idempotent). Safe to call again
    to retry after an error -- finished steps are skipped."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_run, daemon=True)
        _thread.start()


def is_complete():
    """True once provisioning has finished successfully (used for first-run
    routing). Trusts the flag; the steps themselves re-verify on disk."""
    from dotenv import dotenv_values
    from config import ENV_PATH
    return dotenv_values(ENV_PATH).get("PROVISIONING_COMPLETE") == "1"
