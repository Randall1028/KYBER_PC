"""
config.py -- KYBER PC's real configuration layer: .env loading, droid
identity, and the personality trait system. This replaces the hardcoded
stand-ins used throughout the earlier test scripts (kyber_pc_test.py etc.)
with actual, editable config -- the same way kyber_core.py does on Pi.

TRAIT_LINES, personality_summary(), and build_personality_block() below are
lifted directly from kyber_core.py, not reinvented -- same sentences, same
slider-to-text mapping, same 1-5 scale. Kalvin confirmed the Pi build has
been frozen/stable since before PC work started, so this should be an exact
match to what's actually running there.

Usage:
    from config import DROID_NAME, DROID_MAC, DROID_TYPE, load_personality_traits, build_personality_block

    traits = load_personality_traits()
    personality_block = build_personality_block(traits)
"""

import json
import os
import shutil
import sys

from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

# SOURCE_DIR is where this file lives -- used to locate sibling scripts when
# running from source, and (with _MEIPASS) for read-only bundled assets. It is
# NOT where writable data goes.
SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

# PROJECT_DIR is where ALL writable/mutable state lives: .env, personality_maps,
# the downloaded Ollama runtime + models, and the *_result.json worker
# handshakes. From source that's just the repo folder (self-contained dev).
# Once installed, the exe may sit anywhere the user chose -- including a
# read-only spot like Program Files -- so writable state instead goes to the
# per-user app-data folder (%LocalAppData%\KYBER): the standard Windows home for
# large downloaded data and mutable config, writable without admin and safe
# across app updates/reinstalls. Every process resolves the same path here, so
# the file-based worker handshakes still line up.
if getattr(sys, "frozen", False):
    _appdata = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    PROJECT_DIR = os.path.join(_appdata, "KYBER")
else:
    PROJECT_DIR = SOURCE_DIR
os.makedirs(PROJECT_DIR, exist_ok=True)
ENV_PATH = os.path.join(PROJECT_DIR, ".env")
MAP_DIR = os.path.join(PROJECT_DIR, "personality_maps")


def _bundle_dir() -> str:
    """Where read-only bundled assets live: PyInstaller's extraction dir when
    frozen, else this file's own folder."""
    return sys._MEIPASS if getattr(sys, "frozen", False) else SOURCE_DIR


def _read_version() -> str:
    """Single source of truth for the app version -- the VERSION file, which is
    bundled with the app AND read by the installer (KYBER_Setup.iss) at compile
    time, so the page footers and the installer version can never drift apart.
    Bump the version by editing that one file."""
    try:
        with open(os.path.join(_bundle_dir(), "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


APP_VERSION = _read_version()


def seed_default_maps() -> None:
    """On a fresh install MAP_DIR is empty, so the personality picker shows no
    neutral slots and no character archetypes. Copy the bundled seed files
    (default_maps/: the four locked character profiles + five neutral
    'Personality N' slots) into MAP_DIR, filling in ONLY what's missing so a
    profile the user later edits or saves is never overwritten."""
    src = os.path.join(_bundle_dir(), "default_maps")
    if not os.path.isdir(src):
        return
    os.makedirs(MAP_DIR, exist_ok=True)
    for name in os.listdir(src):
        dst = os.path.join(MAP_DIR, name)
        if not os.path.exists(dst):
            try:
                shutil.copyfile(os.path.join(src, name), dst)
            except Exception:
                pass


seed_default_maps()

load_dotenv(ENV_PATH)

# --- Droid identity ---
DROID_MAC = os.getenv("DROID_MAC", "")
DROID_NAME = os.getenv("DROID_NAME", "")
DROID_TYPE = os.getenv("DROID_TYPE", "R")

# --- Personality / sound profile slots ---
ACTIVE_PERSONALITY = os.getenv("ACTIVE_PERSONALITY", "1")
ACTIVE_SOUND_PROFILE = os.getenv("ACTIVE_SOUND_PROFILE", "1")

# --- Motor calibration ---
CALIBRATION_LEFT_SCALE = float(os.getenv("CALIBRATION_LEFT_SCALE", "1.0"))
CALIBRATION_RIGHT_SCALE = float(os.getenv("CALIBRATION_RIGHT_SCALE", "1.0"))

# --- Feature flags ---
BEACON_RELAY_ENABLED = os.getenv("BEACON_RELAY_ENABLED", "true").lower() == "true"
ROAM_MODE_ENABLED = os.getenv("ROAM_MODE_ENABLED", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Model tier -- capable PCs get the full 4B brain; weak machines (2-core CPUs
# or under 8 GB RAM) silently get a lighter 1.5B that actually runs at a
# conversational pace. Auto-detected ONCE on first run and pinned in .env as
# KYBER_TIER; the user never sees or chooses it. Everything else -- the prompt,
# temperature (0.0), Whisper -- is identical across tiers; only the Ollama model
# differs. This is the single source of truth for the model tag, shared by
# kyber_core.py and provisioning.py so the running brain and the downloader can
# never disagree.
# ---------------------------------------------------------------------------
TIER_MODELS = {
    "full": "qwen3:4b-instruct-2507-q4_K_M",   # capable PCs (unchanged default)
    "lite": "qwen2.5:1.5b-instruct-q4_K_M",     # 2-core / low-RAM machines
}
DEFAULT_TIER = "full"


def _total_ram_gb() -> float:
    """Physical RAM in GB with no external dependency. Returns a large number if
    it can't be determined, so a detection failure never wrongly forces Lite."""
    try:
        if os.name == "nt":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)
    except Exception:
        return 999.0


def detect_tier() -> str:
    """Pick a tier from the hardware alone: 'lite' for weak machines (2 or fewer
    logical CPUs, or under 8 GB RAM), 'full' otherwise. Silent by design."""
    cores = os.cpu_count() or 4
    if cores <= 2 or _total_ram_gb() < 8:
        return "lite"
    return "full"


def active_tier() -> str:
    """The tier currently in force, read fresh from the environment/.env and
    defaulting safely to full if unset or unrecognized."""
    t = (os.getenv("KYBER_TIER", "") or "").strip().lower()
    return t if t in TIER_MODELS else DEFAULT_TIER


def active_ollama_model() -> str:
    """The Ollama model tag for the active tier -- what both the brain and the
    provisioner use, so they can never drift apart."""
    return TIER_MODELS[active_tier()]


def ensure_tier() -> str:
    """Detect and PIN the tier once, on first run. If KYBER_TIER is already set
    (a prior run, or a manual override someone put in .env), it is left alone.
    Called by provisioning before the model download so the right-sized model
    gets pulled. Returns the active tier."""
    existing = (os.getenv("KYBER_TIER", "") or "").strip().lower()
    if existing in TIER_MODELS:
        return existing
    tier = detect_tier()
    update_env_values({"KYBER_TIER": tier})
    os.environ["KYBER_TIER"] = tier  # visible to this process immediately
    return tier


def update_env_values(values: dict) -> None:
    """Writes one or more key/value pairs to the real .env file on disk,
    creating the file first if it doesn't exist yet. Uses python-dotenv's
    own set_key, which updates just the given keys in place without
    touching any other lines already in the file.

    This was missing entirely -- kyber_config_server.py, claim_worker.py,
    and activation_worker.py all already import and call this, but nothing
    in config.py actually defined it until now.

    Important: this only updates the .env file on disk. It does NOT update
    this module's own DROID_NAME/DROID_TYPE/etc. constants above, since
    those were read once via os.getenv() at import time -- Python has no
    way to know to re-run that. Anywhere that needs the latest value right
    after calling this should re-read with dotenv_values(ENV_PATH) rather
    than trusting these module-level constants.
    """
    if not os.path.exists(ENV_PATH):
        open(ENV_PATH, "a").close()
    for key, value in values.items():
        set_key(ENV_PATH, key, str(value))


# ---------------------------------------------------------------------------
# Personality traits -- faithful copy of kyber_core.py's real system
# ---------------------------------------------------------------------------

def _personality_map_path() -> str:
    """ACTIVE_PERSONALITY is either a custom slot number ("1".."5") or one of
    the locked default ids ("r2d2", "bb8", "chopper", "bd1", "aseries") --
    each addresses a different file (personality_N.json vs
    personality_default_*.json). Same convention as Pi."""
    if ACTIVE_PERSONALITY.isdigit():
        return os.path.join(MAP_DIR, f"personality_{ACTIVE_PERSONALITY}.json")
    return os.path.join(MAP_DIR, f"personality_default_{ACTIVE_PERSONALITY}.json")


def load_personality_traits() -> dict:
    defaults = {"brave": 3, "curious": 3, "sassy": 3, "playful": 3, "sensitive": 3}
    map_path = _personality_map_path()
    if os.path.exists(map_path):
        try:
            with open(map_path, "r") as f:
                data = json.load(f)
            traits = data.get("traits", {})
            return {k: traits.get(k, v) for k, v in defaults.items()}
        except Exception as e:
            print(f"[WARN]: Could not load personality traits: {e}", flush=True)
    else:
        print(f"[WARN]: No personality file found for '{ACTIVE_PERSONALITY}' -- using neutral traits", flush=True)
    return defaults


# Turns a 1-5 trait slider into a sentence the LLM can use directly, instead
# of a bare number it has to guess the meaning of. Identical to kyber_core.py.
TRAIT_LINES = {
    "brave": [
        "You are highly cautious and avoid risk whenever possible.",
        "You are mostly cautious but can show confidence when it counts.",
        "You balance caution with confidence.",
        "You are confident and willing to take initiative.",
        "You act boldly and rarely hesitate, even in risky situations.",
    ],
    "sassy": [
        "You are very polite and rarely show attitude.",
        "You are mostly polite but may show mild attitude occasionally.",
        "You sometimes respond with light sarcasm.",
        "You often respond with sarcasm or attitude.",
        "You are highly sarcastic and frequently respond with strong attitude.",
    ],
    "curious": [
        "You rarely question things and prefer not to explore.",
        "You are slightly curious but not very proactive.",
        "You are moderately curious and occasionally ask questions.",
        "You actively explore and ask questions about things.",
        "You are extremely curious and will push into situations to find answers, even if it gets you into trouble.",
    ],
    "sensitive": [
        "You are emotionally tough and rarely affected by negativity.",
        "You are somewhat resilient and not easily upset.",
        "You are moderately sensitive to tone and emotion.",
        "You are emotionally reactive and can be affected by negativity.",
        "You are highly sensitive and strongly react to emotional tone or perceived criticism.",
    ],
    "playful": [
        "You are serious and rarely joke or play.",
        "You are mostly serious but occasionally lighthearted.",
        "You balance seriousness with some playful behavior.",
        "You are playful and often joke or tease.",
        "You are highly playful and frequently joke, tease, and act mischievous.",
    ],
}


def personality_summary(sliders: dict) -> str:
    """One-line gist before the detailed trait lines -- keeps the model
    coherent across five separate sentences instead of treating each trait
    in isolation."""
    traits = []
    if sliders["brave"] >= 4: traits.append("bold")
    elif sliders["brave"] <= 2: traits.append("cautious")
    if sliders["curious"] >= 4: traits.append("curious")
    elif sliders["curious"] <= 2: traits.append("indifferent")
    if sliders["playful"] >= 4: traits.append("playful")
    elif sliders["playful"] <= 2: traits.append("serious")
    if sliders["sassy"] >= 4: traits.append("sarcastic")
    elif sliders["sassy"] <= 2: traits.append("polite")
    if sliders["sensitive"] >= 4: traits.append("emotionally reactive")
    elif sliders["sensitive"] <= 2: traits.append("emotionally resilient")
    if not traits:
        return "You have a balanced and adaptable personality."
    return "You are a " + ", ".join(traits) + " droid."


def build_personality_block(sliders: dict) -> str:
    lines = []
    for trait, options in TRAIT_LINES.items():
        idx = max(1, min(5, sliders.get(trait, 3))) - 1  # clamp, just in case
        lines.append(options[idx])
    return personality_summary(sliders) + "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Emotion -> sound bank map -- faithful copy of kyber_core.py's DEFAULT_EMOTION_MAP
# ---------------------------------------------------------------------------

DEFAULT_EMOTION_MAP = {
    "confused":  [{"bank_id":1,"sound_id":1},{"bank_id":1,"sound_id":3},{"bank_id":1,"sound_id":4},{"bank_id":2,"sound_id":4},{"bank_id":3,"sound_id":3},{"bank_id":4,"sound_id":1}],
    "curious":   [{"bank_id":1,"sound_id":1},{"bank_id":2,"sound_id":4},{"bank_id":4,"sound_id":1},{"bank_id":6,"sound_id":1},{"bank_id":6,"sound_id":4}],
    "neutral":   [{"bank_id":1,"sound_id":1},{"bank_id":2,"sound_id":4},{"bank_id":3,"sound_id":2},{"bank_id":3,"sound_id":3},{"bank_id":6,"sound_id":4}],
    "angry":     [{"bank_id":1,"sound_id":2},{"bank_id":1,"sound_id":3},{"bank_id":2,"sound_id":1},{"bank_id":2,"sound_id":2},{"bank_id":2,"sound_id":3},{"bank_id":9,"sound_id":1}],
    "disgusted": [{"bank_id":1,"sound_id":2},{"bank_id":2,"sound_id":1},{"bank_id":2,"sound_id":2},{"bank_id":2,"sound_id":3}],
    "sad":       [{"bank_id":1,"sound_id":3},{"bank_id":1,"sound_id":4},{"bank_id":7,"sound_id":1},{"bank_id":7,"sound_id":2},{"bank_id":7,"sound_id":3},{"bank_id":7,"sound_id":4},{"bank_id":7,"sound_id":5}],
    "defensive": [{"bank_id":1,"sound_id":3},{"bank_id":1,"sound_id":4},{"bank_id":3,"sound_id":3},{"bank_id":4,"sound_id":1},{"bank_id":7,"sound_id":1},{"bank_id":7,"sound_id":2},{"bank_id":7,"sound_id":3},{"bank_id":7,"sound_id":4}],
    "scared":    [{"bank_id":1,"sound_id":4},{"bank_id":4,"sound_id":1},{"bank_id":5,"sound_id":1},{"bank_id":7,"sound_id":1},{"bank_id":7,"sound_id":3},{"bank_id":7,"sound_id":4}],
    "happy":     [{"bank_id":3,"sound_id":1},{"bank_id":3,"sound_id":2},{"bank_id":3,"sound_id":3},{"bank_id":6,"sound_id":1},{"bank_id":6,"sound_id":2},{"bank_id":6,"sound_id":3},{"bank_id":6,"sound_id":4}],
    "excited":   [{"bank_id":3,"sound_id":1},{"bank_id":3,"sound_id":2},{"bank_id":6,"sound_id":1},{"bank_id":6,"sound_id":2},{"bank_id":6,"sound_id":3}],
}


# ---------------------------------------------------------------------------
# Frozen-aware subprocess relaunch
# ---------------------------------------------------------------------------
# Every KYBER helper process is launched by re-invoking the SAME program with a
# --mode flag. From source that's `python <script.py>`; once frozen it's
# `KYBER.exe --mode <name>` (there's no python.exe to hand a script to). Both
# tray_shell and kyber_config_server build their launch commands through this
# one helper so the source path and the frozen path can never drift apart.

_MODE_SCRIPTS = {
    "tray": "tray_shell.py",
    "mainframe": "kyber_config_server.py",
    "core": "kyber_core.py",
    "claim": "claim_worker.py",
    "claim_connect": "claim_connect_worker.py",
    "sound_mapper": "sound_discovery_server.py",
}


def relaunch_command(mode, *extra):
    """Command list to launch KYBER in the given mode. Frozen -> the exe plus a
    --mode flag; from source -> python running the matching sibling script
    (byte-for-byte the pre-packaging behavior)."""
    if mode not in _MODE_SCRIPTS:
        raise ValueError("Unknown relaunch mode: %r" % (mode,))
    tail = [str(x) for x in extra]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mode", mode] + tail
    return [sys.executable, os.path.join(SOURCE_DIR, _MODE_SCRIPTS[mode])] + tail
