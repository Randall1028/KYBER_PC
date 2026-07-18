"""
kyber_core.py -- KYBER's real PC brain: hear (Whisper) -> classify (Qwen3)
-> physically REACT (real sound + dome movement on the connected droid) --
while Beacon Relay's broadcast + scan roles run at the same time.

Renamed from kyber_pc_test.py now that it's wired to real config (config.py)
instead of hardcoded stand-ins -- droid identity and personality now come
from .env / personality_maps/, the same system kyber_core.py uses on Pi.

TWO SEPARATE PROCESSES, not threads. History: this started as one process
with everything on the main asyncio loop plus a background thread for
Whisper/Qwen3. That kept hitting a "Thread is configured for Windows GUI
but callbacks are not working" error from bleak's WinRT backend -- first at
the initial scan, then again inside pyDroidDepot's internal reconnect scan,
then a silent, traceback-less crash loading the Whisper model itself. Each
fix (deferring imports, moving work to the main thread) solved that specific
spot, and the same underlying conflict kept resurfacing somewhere else --
heavy native Bluetooth/WinRT work and heavy native audio/ML work sharing one
process's low-level Windows/COM state was fundamentally fragile, not any one
specific line of code.

The actual fix: separate OS processes. Each gets its own independent memory
and COM state on Windows, so nothing either side does can touch the other at
all. One process owns the droid connection and Beacon Relay's BLE roles; the
other owns mic capture, Whisper, and Qwen3. They talk to each other through
one plain multiprocessing.Queue -- the Whisper/Qwen3 process pushes a
classified emotion (or "confused" for a glitch) onto it; the BLE process
picks it up and actually plays the sound + dome movement on the droid.

Reaction logic (DEFAULT_EMOTION_MAP, dome movements, the sound+motor dispatch
pattern) is lifted directly from kyber_core.py's real play_emotion(), not
reinvented -- same sound bank IDs, same head-rotation directions/speeds.
Deliberately scoped to the always-on baseline reaction only (sound + dome
movement); the additional expressive-mode body-gesture layer from Pi isn't
built for PC yet.

Still not fully "real": DROID_MAC and DROID_TYPE are loaded from config but
not actively used yet -- droid discovery still always scans rather than
connecting directly by a known MAC, and dome movements aren't chassis-aware.
Both are reasonable next steps once there's an onboarding wizard to actually
populate DROID_MAC via a real Claim step, and worth testing properly rather
than assuming they work, the same way everything else here got verified.

Run directly:
    python kyber_core.py
"""

from __future__ import annotations  # defers all type-annotation evaluation --
                                     # needed because several names below are
                                     # None at definition time (see the two
                                     # _import_*_libs() functions) and
                                     # annotations referencing them would
                                     # otherwise crash on import

import asyncio
import json
import multiprocessing
import os
import queue
import random
import re
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from config import (
    ACTIVE_PERSONALITY,
    BEACON_RELAY_ENABLED,
    CALIBRATION_LEFT_SCALE,
    CALIBRATION_RIGHT_SCALE,
    DEFAULT_EMOTION_MAP,
    DROID_MAC,
    DROID_NAME,
    DROID_TYPE,
    ENV_PATH,
    MAP_DIR,
    build_personality_block,
    load_personality_traits,
)

# ---------------------------------------------------------------------------
# Lazy-import placeholders. Neither of these groups is imported at true
# module level -- each is only imported inside its own process's entry
# function (_import_ble_libs / _import_audio_libs), so when multiprocessing
# re-imports this module fresh in each child process (Windows uses "spawn",
# a genuinely new Python interpreter per process), neither process ever
# loads the OTHER side's libraries at all. That's what makes the process
# split actually work -- not just running in parallel, but never sharing
# the conflicting native library state in the first place.
# ---------------------------------------------------------------------------

# BLE / droid / Beacon Relay side
BleakScanner = None
BeaconDeviceTypes = None
decode_dbeacon = None
dbeacon_utils = None
DroidConnection = None
DisneyBLEManufacturerId = None
DroidScriptEngine = None
DroidCommandId = None
DroidBluetoothCharacteristics = None
int_to_hex = None
DroidScripts = None
BluetoothLEAdvertisement = None
BluetoothLEAdvertisementPublisher = None
BluetoothLEAdvertisementWatcher = None
BluetoothLEManufacturerData = None
DataWriter = None

# Whisper / audio side
np = None
sd = None
WhisperModel = None


def _import_ble_libs():
    global BleakScanner, BeaconDeviceTypes, decode_dbeacon, dbeacon_utils
    global DroidConnection, DisneyBLEManufacturerId, DroidScriptEngine, DroidScripts
    global BluetoothLEAdvertisement, BluetoothLEAdvertisementPublisher
    global BluetoothLEAdvertisementWatcher, BluetoothLEManufacturerData, DataWriter
    global DroidCommandId, DroidBluetoothCharacteristics, int_to_hex

    from bleak import BleakScanner as _BleakScanner
    from dbeacon.beacon import BeaconDeviceTypes as _BeaconDeviceTypes, decode_dbeacon as _decode_dbeacon
    from dbeacon import utils as _dbeacon_utils
    from droiddepot.connection import DroidConnection as _DroidConnection
    from droiddepot.protocol import (
        DisneyBLEManufacturerId as _DisneyBLEManufacturerId,
        DroidCommandId as _DroidCommandId,
        DroidBluetoothCharacteristics as _DroidBluetoothCharacteristics,
    )
    from droiddepot.utils import int_to_hex as _int_to_hex
    from droiddepot.script import DroidScriptEngine as _DroidScriptEngine, DroidScripts as _DroidScripts
    from winrt.windows.devices.bluetooth.advertisement import (
        BluetoothLEAdvertisement as _BluetoothLEAdvertisement,
        BluetoothLEAdvertisementPublisher as _BluetoothLEAdvertisementPublisher,
        BluetoothLEAdvertisementWatcher as _BluetoothLEAdvertisementWatcher,
        BluetoothLEManufacturerData as _BluetoothLEManufacturerData,
    )
    from winrt.windows.storage.streams import DataWriter as _DataWriter

    BleakScanner = _BleakScanner
    BeaconDeviceTypes = _BeaconDeviceTypes
    decode_dbeacon = _decode_dbeacon
    dbeacon_utils = _dbeacon_utils
    DroidConnection = _DroidConnection
    DisneyBLEManufacturerId = _DisneyBLEManufacturerId
    DroidCommandId = _DroidCommandId
    DroidBluetoothCharacteristics = _DroidBluetoothCharacteristics
    int_to_hex = _int_to_hex
    DroidScriptEngine = _DroidScriptEngine
    DroidScripts = _DroidScripts
    BluetoothLEAdvertisement = _BluetoothLEAdvertisement
    BluetoothLEAdvertisementPublisher = _BluetoothLEAdvertisementPublisher
    BluetoothLEAdvertisementWatcher = _BluetoothLEAdvertisementWatcher
    BluetoothLEManufacturerData = _BluetoothLEManufacturerData
    DataWriter = _DataWriter


def _import_audio_libs():
    global np, sd, WhisperModel
    import numpy as _np
    import sounddevice as _sd
    from faster_whisper import WhisperModel as _WhisperModel
    np = _np
    sd = _sd
    WhisperModel = _WhisperModel


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

# DROID_NAME comes from config.py now, but the .env default is an empty
# string until an onboarding wizard exists to actually set it via a real
# Claim step -- fall back to something coherent for the prompt rather than
# a broken "You are Star Wars droid . Respond..." sentence.
DROID_NAME = DROID_NAME or "your droid"
HISTORY_LENGTH = 8

# ---------------------------------------------------------------------------
# Whisper / audio config
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
BLOCK_DURATION = 0.03
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION)

SILENCE_HANG_SECONDS = 1.0
MIN_SPEECH_SECONDS = 0.3

# Dynamic ambient RMS floor -- ported from Pi's real, hardware-tuned version
# (same constant values) to replace a single fixed threshold, which is what
# was causing the dome "thinking" animation to over-trigger ("fidgeting")
# on ordinary room noise. PC has no WebRTC VAD (Pi gates on VAD AND floor
# together; PC is amplitude-only), so this floor is the whole story here,
# not one half of a pair -- same reasoning as Pi's own comments: a single
# static number can't tell a quiet room from a noisy one, so the floor
# tracks the room's actual ambient level and requires speech to clear it by
# a margin, rising in a loud venue and settling back down in a quiet one.
VAD_RMS_SEED          = 100   # initial ambient estimate on boot
VAD_RMS_MARGIN        = 700   # a chunk must clear the ambient estimate by
                               # this much to count as speech
VAD_RMS_FLOOR_MIN     = 800   # hard floor -- matches the proven-good static
                               # value this is replacing; the tracker can
                               # only ever raise the bar above it, never
                               # lower it (an estimate that collapsed toward
                               # zero during a pause was Pi's own first-
                               # attempt failure mode)
VAD_RMS_FLOOR_MAX     = 1400  # ceiling for a loud room
AMBIENT_TRACK_DOWN_ALPHA = 0.1   # EMA rate when a chunk is quieter than the
                                  # current estimate -- fast, so the floor
                                  # settles back down quickly
AMBIENT_TRACK_UP_ALPHA   = 0.01  # EMA rate when a chunk is louder -- slow,
                                  # so one loud transient can't yank the
                                  # floor up; needs sustained louder ambient
AMBIENT_LOG_DELTA        = 25    # minimum floor change worth a fresh log line

# The floor alone still let a single brief transient (a click, a cough, a
# chair creak -- one chunk that happens to clear the floor) kick off a full
# capture and the dome "thinking" reaction with it. Pi guards against
# exactly this with VAD_SPEECH_START: it requires several consecutive
# VAD-classified speech frames before committing, not just one. PC has no
# spectral classifier to pair with the amplitude floor the way Pi pairs
# VAD-says-speech AND above-floor, so this debounce is the whole story
# here rather than one half of a pair -- require several consecutive
# above-floor chunks before committing to "this is actually speech."
SPEECH_START_CHUNKS = 5   # ~150ms at 30ms/chunk -- close to Pi's own
                           # VAD_SPEECH_START (200ms), scaled to PC's
                           # chunk size
PRE_ROLL_CHUNKS = 5        # kept so the ~150ms it took to debounce isn't
                           # clipped off the front of a confirmed utterance

_ambient_rms_est = float(VAD_RMS_SEED)  # updated only from genuinely idle
                                         # chunks in _whisper_main below --
                                         # never mid-utterance/mid-pause,
                                         # same critical rule as Pi: updating
                                         # on every non-speech chunk (including
                                         # a brief pause between words) drags
                                         # the estimate down while someone's
                                         # still talking, which is exactly
                                         # what made Pi's first attempt at
                                         # this feature get fully reverted.
_last_logged_floor = float(VAD_RMS_FLOOR_MIN)

WHISPER_MODEL_SIZE = "small"

audio_queue: "queue.Queue" = queue.Queue()


def _drain_audio_backlog() -> int:
    """Empties any backlog that piled up in audio_queue while the main
    loop was blocked on something (transcribe_segment, a long gesture
    waited on elsewhere) -- audio_callback runs on its own thread and
    keeps pushing in real time regardless of whether anything is reading,
    so without this the next .get() calls return stale audio captured
    during the blocking period, not fresh current audio. Same underlying
    problem Pi documents for arecord's OS pipe buffer (_discard_mic_backlog),
    same fix in spirit -- PC has no subprocess/pipe to respawn, just a
    plain queue.Queue to empty. Returns how many chunks were discarded,
    for the caller to log if it's worth knowing."""
    discarded = 0
    while True:
        try:
            audio_queue.get_nowait()
            discarded += 1
        except queue.Empty:
            break
    return discarded


def _resolve_wasapi_input_device():
    """Same fix, same reasoning, as kyber_config_server.py's own copy of
    this function -- confirmed via a real log from Kalvin's machine: with
    no explicit device, sd.InputStream() resolves to the legacy MME host
    API, and MME's mapping for this Bluetooth Hands-Free headset goes
    stale ("no driver installed", MME error 6) after a couple of on/off
    cycles, even though the same device stays valid on WASAPI (Windows'
    modern audio API) the whole time. Explicitly resolving the WASAPI
    input device avoids ever falling through to that flaky MME mapping in
    the first place. Returns None (falls back to the previous ambiguous-
    default behavior) if this system has no WASAPI host API at all."""
    try:
        for api in sd.query_hostapis():
            if "wasapi" in api["name"].lower():
                dev = api.get("default_input_device", -1)
                if dev is not None and dev >= 0:
                    return dev
    except Exception:
        pass
    return None


def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"[MIC WARNING]: {status}", file=sys.stderr)
    audio_queue.put(indata.copy())


def load_whisper_model() -> WhisperModel:
    """Runs on CPU deliberately, not as a fallback. faster-whisper's GPU path
    needs system-wide CUDA/cuBLAS libraries that Ollama's own bundled CUDA
    support doesn't provide -- chasing that down isn't worth it when Whisper
    'small' is already fast enough on CPU, and Qwen3 is the model that
    actually benefits from the one GPU on this machine."""
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    print(f"[WHISPER] loaded '{WHISPER_MODEL_SIZE}' on CPU (int8)")
    return model


# Caption-style phrases Whisper invents to fill silence/noise -- it's seen a
# lifetime of YouTube subtitles. These are never real speech to a droid, so
# they're dropped outright. Ambiguous short fillers ("thanks", "you", "bye")
# are NOT listed here on purpose; the per-segment confidence gate below catches
# those when they're hallucinated, without eating them when actually spoken.
_HALLUCINATION_MARKERS = (
    "thank you for watching", "thanks for watching", "see you in the next video",
    "see you next time", "please subscribe", "like and subscribe",
    "dont forget to subscribe", "subtitles by", "amara org",
)


def _normalize_for_hallucination(text: str) -> str:
    """Lowercase, keep only alphanumerics + spaces, collapse whitespace."""
    kept = "".join(c if (c.isalnum() or c.isspace()) else " " for c in text.lower())
    return " ".join(kept.split())


def _looks_like_hallucination(text: str) -> bool:
    n = _normalize_for_hallucination(text)
    if not n:
        return True
    return any(marker in n for marker in _HALLUCINATION_MARKERS)


def transcribe_segment(model: WhisperModel, audio_segment: np.ndarray):
    audio_float = audio_segment.astype(np.float32) / 32768.0
    start = time.time()
    segments, info = model.transcribe(
        audio_float, language="en", vad_filter=True, beam_size=5,
        # Don't let a hallucinated line seed the decode of the next one --
        # that's what turns a single stray "thank you" into a runaway caption.
        condition_on_previous_text=False,
    )
    parts = []
    for seg in segments:
        # Whisper's own probability that this chunk is NOT speech. A high value
        # is exactly when it invents caption text on near-silence, so drop the
        # segment. Real speech scores low here, so genuine short words survive.
        if getattr(seg, "no_speech_prob", 0.0) > 0.6:
            continue
        parts.append(seg.text.strip())
    text = " ".join(p for p in parts if p).strip()
    if _looks_like_hallucination(text):
        text = ""
    elapsed = time.time() - start
    return text, elapsed


# ---------------------------------------------------------------------------
# Qwen3 / Ollama
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"  # not "localhost" -- on Windows, resolving
                                                 # that hostname can try IPv6 first and only
                                                 # fall back to IPv4 after a real fixed delay
from config import active_ollama_model
OLLAMA_MODEL = active_ollama_model()  # tier-driven: 4B on capable PCs, 1.5B on weak CPUs (see config.TIER_MODELS)

# Real personality now, not a hardcoded stand-in -- built from whatever's
# actually in personality_maps/ for ACTIVE_PERSONALITY (config.py). NOTE:
# this initial read is only used as a startup-time fallback reference: the
# actual per-query prompt (_build_system_prompt(), below) re-reads
# ACTIVE_PERSONALITY fresh from .env every time rather than trusting this
# frozen value, which is what makes a personality change from the
# Mainframe's Save actually take effect on the very next query.
_personality_traits = load_personality_traits()
PERSONALITY_BLOCK = build_personality_block(_personality_traits)

VALID_EMOTIONS = [
    "happy", "excited", "sad", "angry", "scared",
    "disgusted", "curious", "confused", "defensive", "neutral",
]

# Sound-category tags that live in an Acoustic Package but are NOT moods the
# brain should ever classify into -- they're sound buckets, not feelings.
NON_MOOD_TAGS = {"start up", "blaster", "thruster", "motor"}


def _active_profile_data() -> dict:
    """Read the active Acoustic Package (sound_profile_N.json) fresh, or {}."""
    from dotenv import dotenv_values as _dv
    slot = _dv(ENV_PATH).get("ACTIVE_SOUND_PROFILE", "1")
    path = os.path.join(MAP_DIR, f"sound_profile_{slot}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def active_mood_meta() -> dict:
    """{mood: {'emoji': str, 'hint': str}} for custom moods in the active
    package. Built-in moods get their emoji from the app palette, not here."""
    return _active_profile_data().get("mood_meta", {}) or {}


def active_moods() -> list:
    """Moods the classifier may pick from: the 10 built-ins plus any custom
    moods in the active package that actually have sounds tagged, minus the
    non-mood sound tags. Built-ins first, customs appended. A mood created but
    not yet pinned to any sound is intentionally NOT offered -- classifying
    into it would only fall back to neutral audio, a silent mismatch."""
    data = _active_profile_data()
    keys = set(data.get("emotion_to_sounds", {}).keys())
    customs = sorted(k for k in keys if k not in VALID_EMOTIONS and k not in NON_MOOD_TAGS)
    return VALID_EMOTIONS + customs


def _mood_list_for_prompt() -> str:
    """Comma-joined mood list for the prompt; custom moods get their hint
    appended in parens to steer the model (matters most on the Lite 1.5B)."""
    meta = active_mood_meta()
    parts = []
    for m in active_moods():
        hint = "" if m in VALID_EMOTIONS else ((meta.get(m) or {}).get("hint") or "").strip()
        parts.append(f"{m} ({hint})" if hint else m)
    return ", ".join(parts)


def _match_mood(raw: str):
    """Parse the model's reply into one active mood, or None. Order matters:
    exact match, then multi-word moods matched as a CONSECUTIVE RUN OF TOKENS
    (so 'happy holidays' beats 'happy', and 'on edge' can't match inside
    'moon edge'), then a single-word mood -- the FIRST token if it's a mood,
    else the first mood token anywhere in the reply.

    That last fallback keeps a stray leading token from glitching an otherwise
    valid answer: with the `->`-primed format the model replies with a bare
    label, but if it ever echoes the arrow or a prefix ('-> sad', 'Mood: sad')
    the label is still the first recognizable mood word, so we take it rather
    than signal a false glitch. Punctuation and the arrow are stripped per
    token so 'sad.' / '->sad' still match."""
    if not raw:
        return None
    cleaned = raw.lower().strip().strip(".,!?\"'")
    moods = active_moods()
    if cleaned in moods:                                   # exact (single or multi-word)
        return cleaned
    tokens = [t.strip(".,!?\"'>-→") for t in cleaned.split()]
    for m in sorted((x for x in moods if " " in x),
                    key=lambda s: len(s.split()), reverse=True):
        mt = m.split()
        for i in range(len(tokens) - len(mt) + 1):
            if tokens[i:i + len(mt)] == mt:                # word-boundary-safe run
                return m
    if tokens and tokens[0] in moods:                      # single-word mood, preferred position
        return tokens[0]
    for tok in tokens:                                     # else first mood word anywhere
        if tok in moods:
            return tok
    return None


def _load_fresh_personality_traits() -> dict:
    """Re-reads ACTIVE_PERSONALITY from .env fresh, rather than trusting
    config.py's own module-level constant (fixed at THIS process's own
    import time, same staleness problem PERSONALITY_BLOCK above has)."""
    from dotenv import dotenv_values as _dv
    active = _dv(ENV_PATH).get("ACTIVE_PERSONALITY", "1")
    defaults = {"brave": 3, "curious": 3, "sassy": 3, "playful": 3, "sensitive": 3}
    if active.isdigit():
        path = os.path.join(MAP_DIR, f"personality_{active}.json")
    else:
        path = os.path.join(MAP_DIR, f"personality_default_{active}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            traits = data.get("traits", {})
            return {k: traits.get(k, v) for k, v in defaults.items()}
        except Exception:
            pass
    return defaults


def _canon_droid(name):
    """Detect a canon celebrity droid from the user's designation, allowing
    common spellings (R2, R2-D2, Artoo, Artoo Deetoo; BB-8, BeeBee Eight;
    Chopper, C1-10P; BD-1). Returns the canonical character name, or None for an
    original/custom droid (which stays faction-agnostic)."""
    n = "".join(c for c in (name or "").lower() if c.isalnum())
    if not n:
        return None
    if n.startswith("r2") or "artoo" in n:
        return "R2-D2"
    if n.startswith("bb8") or n == "bb" or "beebee" in n:
        return "BB-8"
    if "chopper" in n or n == "chop" or n == "c110p":
        return "Chopper (C1-10P)"
    if n.startswith("bd1") or n == "bd" or n == "bdone" or "beedee" in n:
        return "BD-1"
    return None


# Canon villains a good-guy canon droid (R2/BB-8/Chopper/BD-1) treats as enemies.
# The 4B model KNOWS these are villains when asked directly -- but under the
# split-second single-token emotion call, especially inside an excited "we'll see
# X" stream, it doesn't reliably APPLY that (Kylo Ren is the worst offender: he
# carries a lot of competing "complex / redeemed / Ben Solo" association). So when
# an enemy is NAMED in the current line and the droid is canon, we drop the fact
# right on the decision line (see _build_chat_messages). This is strictly
# PER-LINE: a line that names no enemy gets nothing, so it can never bleed
# 'scared' into unrelated lines the way the system-prompt roster did. We give the
# model the FACT (X is an enemy), not the feeling -- so it still reasons the
# situation (enemy arriving -> wary; enemy defeated -> triumphant).
_CANON_ENEMY_NAMES = [
    "darth vader", "kylo ren", "emperor palpatine", "darth sidious",
    "darth maul", "general grievous", "general hux", "captain phasma",
    "the first order", "first order", "stormtroopers", "stormtrooper",
    "the empire", "galactic empire", "inquisitors", "inquisitor",
    "vader", "kylo", "palpatine", "sidious", "grievous", "hux", "phasma",
    "the sith", "sith lord",
]
_ENEMY_RE = re.compile(
    r"\b(" + "|".join(re.escape(nm) for nm in _CANON_ENEMY_NAMES) + r")\b", re.I)


def _named_enemy(text: str):
    """The canon villain named in this line (original casing), or None. Longer
    names are listed first so 'Kylo Ren' wins over bare 'Kylo'."""
    m = _ENEMY_RE.search(text or "")
    return m.group(1) if m else None


def _build_system_prompt(momentum_line: str = "") -> str:
    """Rebuilt fresh on every query rather than a frozen module-level
    constant -- the actual fix for Save Settings not adopting a new
    personality. No live-push endpoint needed (unlike Calibration's
    /calibration_set): since this is read fresh each time anyway, there's
    nothing to push.

    momentum_line is a transient, per-turn soft prior describing the feeling
    still lingering from the previous turn (see _momentum_line). It ALWAYS
    yields to a clear new feeling -- it only colors calm/ambiguous lines -- so
    a scared droid that's then reassured reads happy immediately, not stale."""
    from dotenv import dotenv_values as _dv
    droid_name = _dv(ENV_PATH).get("DROID_NAME", "") or "your droid"
    personality_block = build_personality_block(_load_fresh_personality_traits())
    mood_list = _mood_list_for_prompt()
    # Canon celebrities (detected from the droid's NAME -- R2/R2-D2/Artoo, BB-8/
    # BeeBee Eight, Chopper, BD-1 and common spellings) stay true to their Star
    # Wars selves. An original/custom name gets NO faction or lore lean at all --
    # it reacts by temperament + the situation, so a villain or Imperial build is
    # never force-fed the good-guy bias. Also keeps the prompt lean.
    canon = _canon_droid(droid_name)
    canon_line = ""
    if canon:
        # Canon droids not only HOLD their allegiances, they react from them --
        # a named ally warms them, a named enemy alarms them -- even when the
        # name is only mentioned. Kept inside the canon branch so custom/villain
        # builds still get zero faction lean.
        canon_line = (
            "\nYou are " + canon + " -- a canon Star Wars droid. Stay true to "
            "your character: your loyalties, allies, and enemies are exactly as "
            "they are across all Star Wars films, series, and games. React from "
            "that allegiance even when a name is only mentioned -- warmth, "
            "delight, or excitement toward your friends and allies; wariness, "
            "fear, or anger toward your enemies. Running into or meeting one of "
            "your enemies is never exciting or fun to you, however casually the "
            "plan is framed.\n"
        )
    return f"""You are Star Wars droid {droid_name}. You feel one clear emotion in response to what your User says, and you answer with the single label for that feeling.
{canon_line}
You are fiercely loyal to your User and bonded to them as your own -- they are your person, and you are devoted to them above all.
You know both worlds equally well -- the Star Wars galaxy and real-world Earth (its people, places, food, music, and pop culture). A reference to either is familiar to you, never confusing.

{personality_block}

You always have a strong reaction -- never wishy-washy.
{momentum_line}
Here is what each feeling MEANS. Pick the one whose meaning best fits how you feel:
- happy — warmth, affection, joy; being pleased, glad, or touched; good news; a fond reunion.
- excited — thrill and eager delight; something fun, awesome, or anticipated (a trick to perform, a trip, a favorite ship or ally on the way).
- sad — loss, grief, loneliness, disappointment, bad news, or being left out, rejected, or excluded; sympathy for anyone or anything hurt, forgotten, abandoned, unwanted, or thrown away; a hope that fell through.
- angry — being insulted, wronged, betrayed, mocked, provoked, abandoned, or treated unfairly; the urge to strike back or stand up for yourself and your own.
- scared — a real, immediate physical danger or attack from an OUTSIDE threat, or a dreaded enemy near or on the way. This is fear for your safety. Being left behind, abandoned, ditched, forgotten, replaced, or excluded is NEVER scared -- even when being alone sounds risky, that hurt is sad or angry. Bad news, disappointment, and being scolded are also sad or angry, not scared.
- disgusted — revulsion or distaste; something gross, sickening, wrong, or beneath you.
- curious — genuine intrigue: a mystery, puzzle, or novelty that makes you want to investigate. NOT a fallback for a calm or unclear remark, and separate from your curious temperament.
- confused — ONLY when the User themselves is confused or says something self-contradictory; NEVER your own uncertainty about how to react.
- defensive — bracing to protect yourself or your User; standing your ground against blame, doubt, or a challenge.
- neutral — ordinary small talk, or an empty fragment that genuinely carries no feeling.

The complete set of labels you may answer with (copy ONE exactly, even if it is more than one word): {mood_list}

To decide: work out what your User really MEANS and the mood of the whole exchange, then choose the single label whose meaning best matches how YOU feel about their latest line -- shaped by your personality above (a cold droid meets praise with indifference; a sensitive one takes criticism hard; a bold one isn't easily scared). React to the meaning, not the grammar: a calm sentence, a question, or an unfamiliar Earth reference is never itself a reason to fall back on neutral, curious, or confused. Answer with ONLY that one label, nothing else.

Examples -- you react to the LAST line; the "→" is followed by your one-word answer:
The User just said: "Show us your best barrel roll!"
→ excited

Conversation so far:
User: "That cargo droid hauled crates for us for years."
The User just said: "They finally sent the poor thing off to the scrap heap."
→ sad

The User just said: "The whole mission just got called off."
→ sad

The User just said: "Your worst enemy is right outside, coming for us."
→ scared

The User just said: "North? No -- south. Actually, I have no idea which way to go."
→ confused

The User just said: "I couldn't ask for a better companion than you."
→ happy
"""
def _build_chat_messages(text: str, history: deque, momentum_line: str = "") -> list:
    """Assemble the exact request. The recent conversation is presented as the
    conversation the droid is actually IN (so cumulative mood is FELT, e.g. the
    lead-up that makes a flat line land as sad), and the current line is clearly
    marked as the one to react to. The whole thing mirrors the format of the
    examples in the system prompt -- a short transcript ending in `→` -- so the
    model completes it with one label the same way it saw them demonstrated.

    Two deliberate choices: (1) the droid's OWN past emotion labels are NOT
    replayed as assistant turns (that caused a curious/confused snowball at
    temperature 0); only the User's prior lines carry over, as context. (2) the
    trailing `→` primes a bare label, matching the examples' `→ <label>` shape."""
    messages = [{"role": "system", "content": _build_system_prompt(momentum_line)}]
    recent = [past_text for past_text, _past_emotion in history if past_text]
    lines = []
    if recent:
        lines.append("Conversation so far:")
        lines.extend(f'User: "{t}"' for t in recent)
    lines.append(f'The User just said: "{text}"')
    # Per-line enemy fact: only for canon droids, only when THIS line names a
    # canon villain. Hands the model the allegiance it knows but doesn't reliably
    # apply mid-conversation (see _CANON_ENEMY_NAMES). Stated as a fact, not a
    # feeling, so the model still reads the situation (arriving -> wary, defeated
    # -> triumphant). Lines with no enemy get nothing -> no 'scared' bleed.
    from dotenv import dotenv_values as _dv
    if _canon_droid(_dv(ENV_PATH).get("DROID_NAME", "")):
        enemy = _named_enemy(text)
        if enemy:
            lines.append(f"(Note: {enemy} is one of your enemies -- a threat "
                         "to you and your User, never a friend.)")
    lines.append("→")  # → : primes the one-word answer, matching the examples
    messages.append({"role": "user", "content": "\n".join(lines)})
    return messages


def _get_emotion_qwen_raw(text: str, history: deque, momentum_line: str = "",
                          temperature: float = 0.0) -> str:
    messages = _build_chat_messages(text, history, momentum_line)
    print(f"[QWEN]: waiting on a response... (temp={temperature})", flush=True)
    start = time.time()
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                # temperature is per-attempt: greedy (0.0) first for a stable,
                # correct read; a nonzero value on retry so a malformed reply
                # actually gets a DIFFERENT sample instead of the identical
                # deterministic one (the old retry re-issued the same greedy
                # request and could never recover a parse failure). num_predict
                # has a little headroom so a one-word label prefixed by a stray
                # token ("Mood: happy") isn't truncated before it parses.
                "temperature": temperature,
                "num_predict": 24,
                "num_ctx": 2048,
            },
        },
        timeout=30,
    )
    elapsed = time.time() - start
    print(f"[QWEN]: response in {elapsed:.2f}s", flush=True)
    response.raise_for_status()
    data = response.json()

    def _ns_to_s(ns):
        return f"{ns / 1e9:.2f}s" if ns is not None else "n/a"

    print(
        f"[QWEN]:   load={_ns_to_s(data.get('load_duration'))}  "
        f"prompt_eval={_ns_to_s(data.get('prompt_eval_duration'))} "
        f"({data.get('prompt_eval_count', '?')} tok)  "
        f"generate={_ns_to_s(data.get('eval_duration'))} "
        f"({data.get('eval_count', '?')} tok)",
        flush=True,
    )
    return data.get("message", {}).get("content", "").strip()


def get_emotion_test(text: str, history: deque = None, momentum_line: str = ""):
    if history is None:
        history = deque(maxlen=HISTORY_LENGTH)

    for attempt in range(2):
        # Greedy first pass; if it comes back empty or unparseable, the retry
        # samples with a little temperature so it's a genuinely fresh attempt
        # rather than a byte-identical re-run of the same greedy decode.
        temperature = 0.0 if attempt == 0 else 0.5
        try:
            raw = _get_emotion_qwen_raw(text, history, momentum_line, temperature)
        except requests.exceptions.ConnectionError:
            print("[QWEN ERROR]: Could not reach Ollama -- is it running?", flush=True)
            return None
        except requests.exceptions.Timeout:
            print("[QWEN ERROR]: Ollama took too long to respond.", flush=True)
            return None
        except Exception as e:
            print(f"[QWEN ERROR]: {e}", flush=True)
            return None

        if not raw:
            continue

        emotion = _match_mood(raw)
        if emotion:
            if attempt > 0:
                print(f"[QWEN]: succeeded on retry -- raw was messy, parsed to '{emotion}'", flush=True)
            return emotion

        print(f"[QWEN]: attempt {attempt + 1} didn't parse cleanly -- raw response: {raw!r}", flush=True)

    print("[QWEN]: both attempts failed to produce a valid emotion -- signaling glitch", flush=True)
    return None


# ---------------------------------------------------------------------------
# Real droid reaction -- lifted directly from kyber_core.py's play_emotion()
# ---------------------------------------------------------------------------

LEFT = 0
RIGHT = 8

# DEFAULT_EMOTION_MAP comes from config.py now -- single source of truth
# shared with whatever else eventually needs it, and still the fallback
# whenever no custom sound profile has been saved yet.


def _load_active_sound_map() -> dict:
    """Reads the ACTIVE_SOUND_PROFILE's saved emotion_to_sounds mapping
    fresh from disk every time, falling back to config.py's
    DEFAULT_EMOTION_MAP if no custom profile has been saved for that slot
    yet (or the file/mapping is missing/empty). This is the actual fix for
    Sound Profile not being adopted on Save -- DEFAULT_EMOTION_MAP was a
    static dict in config.py, completely disconnected from what the Sound
    Mapper actually saves to sound_profile_N.json. Same fresh-read shape as
    personality's _load_fresh_personality_traits() -- no restart or
    live-push needed, since this just always reads current."""
    from dotenv import dotenv_values as _dv
    slot = _dv(ENV_PATH).get("ACTIVE_SOUND_PROFILE", "1")
    path = os.path.join(MAP_DIR, f"sound_profile_{slot}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            emotion_map = data.get("emotion_to_sounds", {})
            if emotion_map:
                return emotion_map
        except Exception as e:
            print(f"[SOUND PROFILE] Could not load slot {slot}, falling back to defaults -- {e}", flush=True)
    return DEFAULT_EMOTION_MAP


async def dome_happy(mc):
    try:
        await mc.rotate_head(direction=RIGHT, speed=200)
        await asyncio.sleep(0.4)
        await mc.rotate_head(direction=LEFT, speed=200)
        await asyncio.sleep(0.4)
    finally:
        await mc.center_head()

async def dome_excited(mc):
    try:
        await mc.rotate_head(direction=RIGHT, speed=255)
        await asyncio.sleep(0.3)
        await mc.rotate_head(direction=LEFT, speed=255)
        await asyncio.sleep(0.3)
        await mc.rotate_head(direction=RIGHT, speed=255)
        await asyncio.sleep(0.3)
    finally:
        await mc.center_head()

async def dome_angry(mc):
    try:
        await mc.rotate_head(direction=LEFT, speed=255)
        await asyncio.sleep(0.6)
    finally:
        await mc.center_head()

async def dome_scared(mc):
    try:
        await mc.rotate_head(direction=LEFT, speed=255)
        await asyncio.sleep(0.2)
        await mc.rotate_head(direction=RIGHT, speed=255)
        await asyncio.sleep(0.2)
        await mc.rotate_head(direction=LEFT, speed=255)
        await asyncio.sleep(0.2)
    finally:
        await mc.center_head()

async def dome_sad(mc):
    try:
        await mc.rotate_head(direction=LEFT, speed=100)
        await asyncio.sleep(0.8)
    finally:
        await mc.center_head()

async def dome_curious(mc):
    try:
        await mc.rotate_head(direction=RIGHT, speed=120)
        await asyncio.sleep(0.6)
    finally:
        await mc.center_head()

async def dome_confused(mc):
    try:
        await mc.rotate_head(direction=LEFT, speed=150)
        await asyncio.sleep(0.4)
        await mc.rotate_head(direction=RIGHT, speed=150)
        await asyncio.sleep(0.4)
    finally:
        await mc.center_head()

async def dome_defensive(mc):
    await mc.rotate_head(direction=RIGHT, speed=255)
    await asyncio.sleep(0.7)
    await mc.center_head()

async def dome_about_face(mc):
    await mc.rotate_head(direction=LEFT, speed=120)
    await asyncio.sleep(0.7)
    await mc.center_head()

async def dome_neutral(mc):
    pass


async def dome_thinking(mc):
    """R-series 'thinking' sweep -- covers the ~1-2s STT->Qwen3 latency
    window so the droid appears to react to speech immediately rather than
    sitting frozen while the local models run. Which way the head turns
    first is randomized each call so it doesn't read as a fixed tic.
    BB isn't wired up for this yet -- R-series only, per the punch list."""
    first, second = random.choice([(LEFT, RIGHT), (RIGHT, LEFT)])
    try:
        await mc.rotate_head(direction=first, speed=150)
        await asyncio.sleep(0.5)
        await mc.rotate_head(direction=second, speed=150)
        await asyncio.sleep(0.5)
    finally:
        await mc.center_head()
        # Dome rotation noise from this sweep shouldn't get picked up as
        # the start of a new utterance either -- same mic-gate mechanism
        # react_to_emotion and _play_droid_audio use.
        _stamp_mic_gate()


DOME_MOVEMENTS = {
    "happy":     dome_happy,
    "excited":   dome_excited,
    "angry":     dome_angry,
    "scared":    dome_scared,
    "sad":       dome_sad,
    "curious":   dome_curious,
    "confused":  dome_confused,
    "defensive": dome_defensive,
    "disgusted": dome_about_face,
    "neutral":   dome_neutral,
}


async def _play_droid_audio(droid, sound_id: int, bank_id: int) -> None:
    """Single choke point for every droid audio trigger -- stamps the mic
    gate before playing, mirroring Pi's real _play_droid_audio exactly.
    No completion signal exists in the protocol (play_audio is fire-and-
    forget), so MIC_GATE_DURATION alone is an estimate here, same
    assumption Pi's version makes."""
    _stamp_mic_gate()
    await droid.audio_controller.play_audio(sound_id=sound_id, bank_id=bank_id)


async def react_to_emotion(droid, emotion: str):
    emotion_map = _load_active_sound_map()
    sounds = emotion_map.get(emotion, emotion_map.get("neutral", []))
    if not sounds:
        return
    sound = random.choice(sounds)
    mc = droid.motor_controller
    try:
        await asyncio.sleep(0.8)
        await asyncio.gather(
            _play_droid_audio(droid, sound_id=sound["sound_id"], bank_id=sound["bank_id"]),
            DOME_MOVEMENTS.get(emotion, dome_neutral)(mc),
        )
        print(f"[REACTION]  {emotion} -> bank {sound['bank_id']}, sound {sound['sound_id']} + dome movement", flush=True)
        # Dome rotation has no shared choke point the way leg-drive motors
        # do (_scaled_hold) -- each dome_* function calls mc.rotate_head()
        # directly with its own timing. _play_droid_audio's stamp above
        # only estimates the audio's own duration, which the dome movement
        # can outlast, so this covers the dome side explicitly too. Cheap
        # to call again here regardless -- _stamp_mic_gate() only extends,
        # never shrinks, the gate.
        _stamp_mic_gate()

        # Expressive Mode -- probabilistic physical reaction on top of the
        # normal sound+dome reaction above. angry/disgusted fire 66% of the
        # time, everything else 33%, matching Pi's real play_emotion logic.
        if _expressive_mode_active:
            gesture_chance = _GESTURE_CHANCE.get(emotion, 0.33)
            if random.random() < gesture_chance:
                droid_type = get_droid_type()
                # BB units have a dedicated full-replacement move map; all
                # others use the shared R-tuned EXPRESSIVE_EMOTION_MOVES.
                # This prevents R-unit gestures (tuned at lower absolute
                # power) from firing on a BB chassis.
                active_move_map = _CHASSIS_MOVE_MAPS.get(droid_type, EXPRESSIVE_EMOTION_MOVES)
                candidates = []
                default_fn = active_move_map.get(emotion)
                if default_fn:
                    candidates.append(default_fn)
                # GESTURE_VARIANTS are additive extras (e.g. BD's moonwalk);
                # skipped for chassis types that already have a full
                # dedicated map (currently just BB).
                if droid_type not in _CHASSIS_MOVE_MAPS:
                    candidates += GESTURE_VARIANTS.get((droid_type, emotion), [])
                if candidates:
                    move_fn = random.choice(candidates)
                    print(f"[EXPRESSIVE]: {emotion} motor reaction", flush=True)
                    await move_fn(droid)
    except Exception as e:
        print(f"[REACTION ERROR]  {e}", flush=True)


# ---------------------------------------------------------------------------
# Beacon Relay -- discovery, advertisement, watcher
# ---------------------------------------------------------------------------

TEST_AFFILIATION_ID = 2
TEST_PERSONALITY_ID = 5
DISCOVERY_TIMEOUT_SECONDS = 15


def encode_droid_identification_beacon(droid_paired: bool, affiliation_id: int, personality_id: int) -> str:
    header = dbeacon_utils.int_to_hex(BeaconDeviceTypes.DroidIdentificationBeacon.value) + dbeacon_utils.int_to_hex(4)
    payload = (
        dbeacon_utils.int_to_hex(1 if droid_paired else 0)
        + dbeacon_utils.int_to_hex(affiliation_id)
        + dbeacon_utils.int_to_hex(personality_id)
    )
    return header + payload


def build_advertisement(droid_paired: bool) -> BluetoothLEAdvertisement:
    hex_payload = encode_droid_identification_beacon(droid_paired, TEST_AFFILIATION_ID, TEST_PERSONALITY_ID)
    payload_bytes = bytes.fromhex(hex_payload)

    writer = DataWriter()
    writer.write_bytes(payload_bytes)
    buffer = writer.detach_buffer()

    manufacturer_data = BluetoothLEManufacturerData(DisneyBLEManufacturerId.DroidManufacturerId, buffer)

    advertisement = BluetoothLEAdvertisement()
    advertisement.manufacturer_data.append(manufacturer_data)
    return advertisement


def on_publisher_status_changed(sender, args):
    print(f"[PUBLISHER]  status: {args.status.name}")


def on_advertisement_received(sender, args):
    # Only prints droid-relevant beacons now -- general scanning already
    # proved itself thoroughly earlier (including the independent nRF
    # Connect cross-check). Printing every random nearby device (phones,
    # headphones, TVs, etc.) was drowning out the Whisper process's status
    # lines in this test, with nothing new left to learn from seeing them.
    for md in args.advertisement.manufacturer_data:
        if md.company_id != DisneyBLEManufacturerId.DroidManufacturerId:
            continue
        data = bytes(md.data)
        try:
            decoded = decode_dbeacon(data.hex())
            tag = "  <- our own broadcast" if (
                TEST_AFFILIATION_ID == getattr(decoded, "affiliation_id", None)
                and TEST_PERSONALITY_ID == getattr(decoded, "personality_id", None)
            ) else "  <- a DIFFERENT droid!"
            print(f"[WATCHER]  droid beacon: {decoded}{tag}")
        except Exception as e:
            print(f"[WATCHER]  company_id={md.company_id:#06x}  data={data.hex()}  (decode failed: {e})")


# ---------------------------------------------------------------------------
# Calibration + motor primitives -- ported from Pi's real kyber_core.py.
# The mic-gate omission flagged here previously is done -- see
# _stamp_mic_gate() and _scaled_hold()'s own docstring below for the real
# implementation (now cross-process, since PC splits BLE/Whisper into two
# separate OS processes, unlike Pi's single-process design).
# Everything else -- the write-without-response drive-command fix, the
# confirmed-write stop, the calibration scale math -- is unchanged.
# ---------------------------------------------------------------------------

CALIBRATION_SCALE_MIN = 0.25
CALIBRATION_SCALE_MAX = 3.0
BASE_SPIN_DURATION = 0.5  # "1.0" calibration baseline, measured at true 255 power

# Live-updatable copies -- /calibration_set overwrites these directly so a
# newly-locked-in value applies immediately without restarting the brain.
# config.py's own CALIBRATION_LEFT_SCALE/RIGHT_SCALE stay the source of
# truth for the NEXT process launch; these are just this run's live copy.
_live_calibration_left = CALIBRATION_LEFT_SCALE
_live_calibration_right = CALIBRATION_RIGHT_SCALE

CHASSIS_PROFILES = {
    "r":  {"name": "R-series",  "turn_multiplier": 1.0, "drive_multiplier": 1.0},
    "bb": {"name": "BB-series", "turn_multiplier": 1.0, "drive_multiplier": 1.0},
    "c":  {"name": "C-series",  "turn_multiplier": 1.0, "drive_multiplier": 1.0},
    "a":  {"name": "A-series",  "turn_multiplier": 2.6, "drive_multiplier": 1.12},
    "bd": {"name": "BD-series", "turn_multiplier": 0.9, "drive_multiplier": 7.5},
}


def get_droid_type() -> str:
    """Fresh DROID_TYPE read from .env, lowercased -- same live-reload
    reasoning as before (a droid-model change from the Mainframe's Save
    should affect behavior on the very next reaction), just factored out
    so both chassis motor scaling AND gesture-set dispatch (which chassis
    gets which expressive movement functions) share one source instead of
    each re-reading .env separately."""
    from dotenv import dotenv_values as _dv
    return _dv(ENV_PATH).get("DROID_TYPE", DROID_TYPE).lower()


def get_chassis_profile() -> dict:
    """Reads DROID_TYPE fresh from .env rather than trusting config.py's
    own frozen import-time constant -- same fix as personality's
    _build_system_prompt(), so a droid model change from the Mainframe's
    Save actually affects chassis scaling on the very next gesture."""
    return CHASSIS_PROFILES.get(get_droid_type(), CHASSIS_PROFILES["r"])


def get_calibration_scale(dir0: int, dir1: int) -> float:
    if dir0 == 0 and dir1 == 8:
        return _live_calibration_right
    elif dir0 == 8 and dir1 == 0:
        return _live_calibration_left
    else:
        return (_live_calibration_left + _live_calibration_right) / 2


def get_chassis_scale(dir0: int, dir1: int) -> float:
    profile = get_chassis_profile()
    if (dir0, dir1) in ((0, 8), (8, 0)):
        return profile["turn_multiplier"]
    else:
        return profile["drive_multiplier"]


async def _send_motor_speed_fast(mc, direction: int, motor_id: int, speed: int, ramp_speed: int = 300, delay: int = 0):
    """Forces write-without-response explicitly, rather than leaving it to
    bleak's default (which resolves to the slower, acknowledgment-waiting
    write whenever the characteristic reports both properties -- confirmed
    on real Pi hardware that this was silently costing an extra round trip
    on every single motor command)."""
    delay_hex = int_to_hex(delay)
    if len(delay_hex) < 4:
        delay_hex = delay_hex.rjust(4, '0')
    motor_select = f"{direction}{motor_id}"
    motor_command = f"{motor_select}{int_to_hex(speed)}{int_to_hex(ramp_speed)}{delay_hex}"
    command_bytes = mc.droid.build_droid_command(DroidCommandId.SetMotorSpeed, motor_command)
    await mc.droid.droid.write_gatt_char(
        DroidBluetoothCharacteristics.DroidCommandCharacteristic,
        bytearray.fromhex(command_bytes.hex()),
        response=False,
    )


async def _send_motor_speed_confirmed(mc, direction: int, motor_id: int, speed: int, ramp_speed: int = 300, delay: int = 0):
    """Same wire format as _send_motor_speed_fast, but WITH delivery
    confirmation -- reserved for stop_motors(), the one command whose job
    is safety and that must not be allowed to silently drop."""
    delay_hex = int_to_hex(delay)
    if len(delay_hex) < 4:
        delay_hex = delay_hex.rjust(4, '0')
    motor_select = f"{direction}{motor_id}"
    motor_command = f"{motor_select}{int_to_hex(speed)}{int_to_hex(ramp_speed)}{delay_hex}"
    command_bytes = mc.droid.build_droid_command(DroidCommandId.SetMotorSpeed, motor_command)
    await mc.droid.droid.write_gatt_char(
        DroidBluetoothCharacteristics.DroidCommandCharacteristic,
        bytearray.fromhex(command_bytes.hex()),
        response=True,
    )


# ---------------------------------------------------------------------------
# Mic gate against R2 hearing his own noise -- ported from Pi's real
# _mic_gate_until, which was missing entirely on PC. Confirmed via a real
# log: after every reaction, the droid's own motor/sound noise was
# triggering several false speech captures in a row ("nothing recognized"
# each time) while the dynamic ambient floor scrambled to catch up -- the
# debounce added earlier catches brief transients, but a droid's own
# multi-hundred-ms dome sweep or sound bank is not a brief transient, it's
# sustained loud noise that legitimately clears the floor the same way
# real speech would.
#
# PC's two-process split means this can't be a plain shared global the way
# it is on Pi (single process) -- the BLE process is the one that knows
# when it's making noise, but the Whisper process is the one that needs to
# check before treating a capture as real speech. _mic_gate_until lives
# here as a BLE-process-local global (stamped directly by whatever's about
# to make noise), then mirrored into status_ref every tick (same pattern
# already used for hotel_mode_active) so Whisper can read it the same way
# it already reads mapper_active/activation_suppress_until/hotel_mode_active.
# ---------------------------------------------------------------------------
MIC_GATE_DURATION = 1.5  # trailing margin after noise-making stops, for BLE
                          # latency and mechanical coast-down -- same value
                          # Pi uses (there, reusing POST_PLAY_DELAY)
_mic_gate_until = 0.0


def _stamp_mic_gate(trailing_extra: float = 0.0):
    """Monotonic max, never shrinks an already-later gate -- safe to call
    from multiple overlapping sources (e.g. a sound call and a dome
    movement finishing at different points within the same reaction)."""
    global _mic_gate_until
    candidate = time.time() + trailing_extra + MIC_GATE_DURATION
    _mic_gate_until = max(_mic_gate_until, candidate)


async def _scaled_hold(mc, dir0: int, dir1: int, speed0: int, speed1: int, duration: float, scale: float):
    """Lowest-level motor-leg primitive -- send both motor commands
    concurrently (not sequentially) and hold for duration * scale *
    chassis_scale seconds. Does NOT stop the motors afterward.

    Also the single choke point for gating the mic against leg-motor
    noise -- every drive_leg/drive_hold call, and everything built on them
    (expressive gestures, hotel/pet movement, calibration probes), funnels
    through here, same centralization Pi's own _scaled_hold uses. The real
    hold time is already known exactly, so the gate is precise, not an
    estimate."""
    chassis_scale = get_chassis_scale(dir0, dir1)
    hold_time = duration * scale * chassis_scale
    _stamp_mic_gate(hold_time)
    await asyncio.gather(
        _send_motor_speed_fast(mc, direction=dir0, motor_id=0, speed=speed0),
        _send_motor_speed_fast(mc, direction=dir1, motor_id=1, speed=speed1),
    )
    await asyncio.sleep(hold_time)


async def stop_motors(mc):
    await asyncio.gather(
        _send_motor_speed_confirmed(mc, direction=0, motor_id=0, speed=0),
        _send_motor_speed_confirmed(mc, direction=0, motor_id=1, speed=0),
    )


async def drive_leg(mc, dir0: int, dir1: int, speed0: int, speed1: int, duration: float):
    """Production gesture primitive for a single leg that stops afterward --
    the common case for every expressive gesture. Looks up the live stored
    calibration value automatically; duration is each gesture's own tuned
    literal, untouched -- only the calibration scale multiplies it."""
    scale = get_calibration_scale(dir0, dir1)
    try:
        await _scaled_hold(mc, dir0, dir1, speed0, speed1, duration, scale)
    finally:
        await stop_motors(mc)


async def drive_hold(mc, dir0: int, dir1: int, speed0: int, speed1: int, duration: float):
    """Same as drive_leg but doesn't stop afterward -- for continuous
    multi-phase sequences (Pet) where the next phase overwrites the
    motor command directly. Caller is responsible for stopping once the
    whole sequence finishes."""
    scale = get_calibration_scale(dir0, dir1)
    await _scaled_hold(mc, dir0, dir1, speed0, speed1, duration, scale)


async def calibration_spin_probe(droid, direction: str, scale: float):
    """Calibration-only primitive -- a single full-360 spin probe at an
    explicit candidate scale, bypassing the stored calibration values
    entirely. Used for both the initial unscaled diagnostic spin
    (scale=1.0) and the post-computation verification spin."""
    mc = droid.motor_controller
    dir0, dir1 = (0, 8) if direction == "right" else (8, 0)
    actual_seconds = BASE_SPIN_DURATION * scale
    print(f"[CALIBRATION]: {direction} probe starting -- scale={scale:.3f}, hold={actual_seconds:.3f}s", flush=True)
    try:
        await _scaled_hold(mc, dir0, dir1, 255, 255, BASE_SPIN_DURATION, scale)
    finally:
        await stop_motors(mc)
    print(f"[CALIBRATION]: {direction} probe finished", flush=True)


async def calibration_victory(droid):
    """Celebratory flourish once both directions are confirmed -- always
    runs at the plain default baseline, not whatever candidate scale was
    just confirmed, since this is cosmetic, not a measurement."""
    mc = droid.motor_controller
    print("[CALIBRATION]: victory spin", flush=True)
    try:
        await _scaled_hold(mc, 0, 8, 255, 255, BASE_SPIN_DURATION, 1.0)
    finally:
        await stop_motors(mc)
    sounds = _load_active_sound_map().get("happy", [])
    if sounds:
        s = random.choice(sounds)
        try:
            await _play_droid_audio(droid, sound_id=s["sound_id"], bank_id=s["bank_id"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Expressive gestures -- R-series, ported directly from Pi. The BB-series
# variants (expressive_bb_*) are defined further below with their own emotion
# map (BB_EXPRESSIVE_EMOTION_MOVES / _CHASSIS_MOVE_MAPS); both the manual
# Gestures-page buttons (get_motor_commands) and Expressive Mode dispatch to
# the correct set per chassis. The BB values came over Pi-validated but haven't
# been re-validated on a BB unit here yet -- same caveat as the sound bank list.
# ---------------------------------------------------------------------------

MOTOR_TEST_DIRECTIONS = {
    "forward":  (0, 0),
    "backward": (8, 8),
    "left":     (0, 8),
    "right":    (8, 0),
}
MOTOR_TEST_DURATION_MIN = 0.1
MOTOR_TEST_DURATION_MAX = 3.0  # sane ceiling so a typo can't hold power for a long, unsupervised run


async def expressive_happy_dance(droid):
    """Forward -> 180 spin -> forward -> 360 spin -> forward -> 180 spin."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, 100, 100, 1)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 8, 100, 100, 0.2)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, 100, 100, 1)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 8, 100, 100, 0.6)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, 100, 100, 1)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 8, 0, 100, 100, 0.2)
        print("[EXPRESSIVE]: Happy dance complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_happy_spin(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 8, 255, 255, BASE_SPIN_DURATION)
        print("[EXPRESSIVE]: Happy spin complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_retreat(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, 100, 90, 1)
        print("[EXPRESSIVE]: Retreat complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_angry_charge(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, 255, 255, 1)
        print("[EXPRESSIVE]: Angry charge complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_sad_drift(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, 50, 50, 1.5)
        print("[EXPRESSIVE]: Sad drift complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_curious_nudge(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, 60, 60, 0.7)
        print("[EXPRESSIVE]: Curious nudge complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_defensive_back(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, 100, 100, 1)
        print("[EXPRESSIVE]: Defensive back complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_forward(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, 80, 80, 1.5)
        print("[EXPRESSIVE]: Forward complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_back(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, 70, 70, 1.5)
        print("[EXPRESSIVE]: Back complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_about_face(droid):
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 8, 110, 110, 0.4)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, 190, 190, 1)
        print("[EXPRESSIVE]: About Face move complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_moonwalk_bd(droid):
    """Named for its BD origins, but Pi's own /motor dispatch maps
    'moonwalk' to this function unconditionally regardless of chassis --
    preserved exactly as Pi has it, not chassis-gated here either."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, 100, 100, 1)
        await drive_leg(mc, 0, 8, 100, 100, 0.6)
        await drive_leg(mc, 8, 8, 100, 100, 1)
        print("[EXPRESSIVE]: Moonwalk complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def hotel_move(droid):
    """AC-sensor-triggering patrol movement -- straight patrol pattern,
    spins mirrored (first right, then left) so repeated cycles don't
    accumulate rotation in one direction."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, 190, 190, 1)
        await asyncio.sleep(5)
        await drive_leg(mc, 0, 8, 110, 110, 0.4)
        await drive_leg(mc, 0, 0, 190, 190, 1)
        await asyncio.sleep(5)
        await drive_leg(mc, 8, 0, 110, 110, 0.4)
        print("[HOTEL]: Movement complete", flush=True)
    except Exception as e:
        print(f"[HOTEL MOVE ERROR]: {e}", flush=True)


async def hotel_move_bb(droid):
    """BB -- Hotel Sentry: forward, a real 10-12s pause to let the ball
    settle and establish a cold launch, a randomized-direction 180 at the
    "Absolute Cold Launch" values, then forward again. The long pause is
    deliberate -- BB needs to be genuinely stationary (not just paused
    between legs) for the 180 to be a true cold launch rather than an
    in-sequence one."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, BB_FWD_SPEED, BB_FWD_SPEED, 1.0)
        await asyncio.sleep(random.uniform(10.0, 12.0))
        d0, d1 = random.choice([(0, 8), (8, 0)])
        await drive_leg(mc, d0, d1, BB_180_SPEED, BB_180_SPEED, BB_180_DURATION)
        await drive_leg(mc, 0, 0, BB_FWD_SPEED, BB_FWD_SPEED, 1.0)
        print("[HOTEL]: BB movement complete", flush=True)
    except Exception as e:
        print(f"[HOTEL BB MOVE ERROR]: {e}", flush=True)


async def hotel_move_v2(droid):
    """Evaluation rig -- two About Face gestures back to back, each
    starting from a real stop."""
    try:
        await expressive_about_face(droid)
        await asyncio.sleep(2.0)
        await expressive_about_face(droid)
        print("[HOTEL V2]: Movement complete", flush=True)
    except Exception as e:
        print(f"[HOTEL V2 MOVE ERROR]: {e}", flush=True)


def get_motor_commands() -> dict:
    """Built fresh on every call, not a static module-level dict -- same
    command name ("happy_dance" etc.) needs to resolve to a different
    function depending on the CURRENT chassis, matching Pi's real
    handle_motor exactly (which rebuilds this same dict per-request for
    the same reason). The Gestures page's buttons themselves are already
    chassis-agnostic on the frontend, same as Pi's -- this is the backend
    half of that: one "Happy Dance" button, dispatched to the right
    implementation based on DROID_TYPE. moonwalk and hotel_v2 are
    unconditional regardless of chassis, matching Pi exactly."""
    is_bb = get_droid_type() == "bb"
    return {
        "happy_dance":    expressive_bb_happy_dance    if is_bb else expressive_happy_dance,
        "happy_spin":     expressive_bb_happy_spin     if is_bb else expressive_happy_spin,
        "retreat":        expressive_bb_retreat        if is_bb else expressive_retreat,
        "angry_charge":   expressive_bb_angry_charge   if is_bb else expressive_angry_charge,
        "sad_drift":      expressive_bb_sad_drift      if is_bb else expressive_sad_drift,
        "curious_nudge":  expressive_bb_curious_nudge  if is_bb else expressive_curious_nudge,
        "defensive_back": expressive_bb_defensive_back if is_bb else expressive_defensive_back,
        "about_face":     expressive_bb_about_face     if is_bb else expressive_about_face,
        "moonwalk":       expressive_moonwalk_bd,
        "forward":        expressive_bb_forward        if is_bb else expressive_forward,
        "back":           expressive_bb_back           if is_bb else expressive_back,
        "hotel":          hotel_move_bb                if is_bb else hotel_move,
        "hotel_v2":       hotel_move_v2,
    }


# ---------------------------------------------------------------------------
# Protocols/Subroutines -- Hotel Sentry, Expressive Mode, Pet Entertainer.
# Ported from Pi, including voice-command keyword triggers (check_keywords/
# handle_keyword, near the bottom of this section) and the LED-flash
# keepalive ping (keepalive_thread) -- the two pieces originally deferred,
# now built. One piece removed entirely rather than ported: Autonomous
# Roam isn't shipping in V1 at all, per Kalvin's call, so the toggle, the
# thread, the movement/sound burst, and the mutual-exclusivity handling are
# all gone rather than just hidden.
# Hotel/Pet are mutually exclusive (activating one deactivates the other);
# Expressive Mode is independent and runs alongside anything else, matching
# Pi's real behavior exactly.
# ---------------------------------------------------------------------------

HOTEL_DURATION = 8 * 3600       # 8 hours, auto-deactivates after this
HOTEL_MOVE_INTERVAL = 15 * 60   # move to trigger AC sensor every 15 minutes
PET_INTERVALS = [30, 2 * 60, 3 * 60]
KEEPALIVE_INTERVAL = 90         # LED-flash ping sent to the droid on this
                                # cadence while active -- keeps the droid's
                                # own auto-sleep timer (shorter than Hotel
                                # Sentry's 15-minute move interval) from
                                # ever having a long enough gap to trigger

# Fixed factory bank/sound IDs -- these three keyword confirmations bypass
# the customizable sound-profile emotion map entirely, same as Pi, since
# they're tied to specific hardware banks rather than an emotion.
STAY_AWAKE_SOUNDS = [
    {"bank_id": 3, "sound_id": 1},
    {"bank_id": 3, "sound_id": 2},
    {"bank_id": 3, "sound_id": 3},
    {"bank_id": 6, "sound_id": 1},
    {"bank_id": 6, "sound_id": 2},
    {"bank_id": 6, "sound_id": 3},
    {"bank_id": 6, "sound_id": 4},
]
GO_TO_SLEEP_SOUNDS = [
    {"bank_id": 1, "sound_id": 3},
    {"bank_id": 1, "sound_id": 4},
    {"bank_id": 7, "sound_id": 1},
    {"bank_id": 7, "sound_id": 2},
    {"bank_id": 7, "sound_id": 3},
    {"bank_id": 7, "sound_id": 4},
]

_hotel_mode_active = False
_hotel_end_time = 0.0
_hotel_activated_time = 0.0
_pet_mode_active = False
_expressive_mode_active = False
_keepalive_active = False

# Emotion -> gesture map for Expressive Mode's probabilistic physical
# reactions. This is the R-series (default) map; BB has its own
# BB_EXPRESSIVE_EMOTION_MOVES below, chosen per-chassis via _CHASSIS_MOVE_MAPS
# in react_to_emotion, so BB units get BB-tuned expressive gestures too.
EXPRESSIVE_EMOTION_MOVES = {
    "happy":     expressive_happy_spin,
    "excited":   expressive_happy_dance,
    "scared":    expressive_retreat,
    "angry":     expressive_angry_charge,
    "sad":       expressive_sad_drift,
    "curious":   expressive_curious_nudge,
    "defensive": expressive_defensive_back,
    "disgusted": expressive_about_face,
}
# angry/disgusted fire a gesture 66% of the time when expressive mode is
# active; everything else defaults to 33% (see the .get() fallback below).
_GESTURE_CHANCE = {
    "angry": 0.66,
    "disgusted": 0.66,
}

# ---------------------------------------------------------------------------
# BB-series gesture set -- ported from Pi's real, hardware-validated values
# (tuned on Nub, 8N-UB), not scaled-down guesses. Dedicated functions, not
# modifications of R-unit values -- BB is a ball droid, not a wheeled biped,
# so its movement vocabulary is genuinely different, not just faster/slower
# versions of the R-series moves. C-series and A-series need none of this:
# they already work correctly today through CHASSIS_PROFILES/
# get_chassis_scale alone, reusing the R-tuned functions below scaled by
# their own turn_multiplier/drive_multiplier.
#
# Dome motor commands (rotate_head/center_head) are intentionally omitted
# here too, matching Pi exactly -- BB head-motor behavior hasn't been
# separately confirmed on hardware, so BB just runs the same shared
# sound+dome path as everything else, and the existing exception handling
# in react_to_emotion/_safe_thinking_anim already absorbs whatever a dome
# command does on a chassis that doesn't have a head the way R does.
# ---------------------------------------------------------------------------
BB_SPIN_SPEED    = 255   # validated spin/turn speed for full rotations (happy spin, dance)
BB_QUICK_SPEED   = 195   # cold-launch reaction spin speed for Pet
BB_FWD_SPEED     = 200   # validated safe forward/backward ceiling (also Angry Charge's forward power)
BB_SLOW_SPEED    = 130   # gentle speed for drift/nudge gestures (~65% of fwd)
BB_RETREAT_SPEED = 110   # Defensive reverse power (calm withdrawal)
BB_SCARED_SPEED  = 255   # Scared reverse power (panicked, full power -- distinct from Defensive)
BB_180_SPEED     = 215   # "Absolute Cold Launch" 180 -- About Face + Hotel Sentry
BB_SPIN_FULL     = 0.60  # duration for full 360 deg at BB_SPIN_SPEED
BB_SPIN_HALF     = 0.15  # 180 deg approximation for Happy Dance
BB_SPIN_QUICK    = 0.10  # quick reaction spin duration for Pet
BB_180_DURATION  = 0.30  # duration for the 180 cold-launch move at BB_180_SPEED


async def expressive_bb_happy_dance(droid):
    """BB -- forward -> 180 spin -> forward -> 360 spin -> forward -> 180 spin back."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, BB_FWD_SPEED,  BB_FWD_SPEED,  0.8)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 8, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_HALF)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, BB_FWD_SPEED,  BB_FWD_SPEED,  0.8)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 8, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_FULL)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, BB_FWD_SPEED,  BB_FWD_SPEED,  0.8)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 8, 0, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_HALF)
        print("[EXPRESSIVE]: BB happy dance complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_happy_spin(droid):
    """BB -- single cold-launch 360 spin."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 8, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_FULL)
        print("[EXPRESSIVE]: BB happy spin complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_retreat(droid):
    """BB -- Scared Retreat: full-power reverse burst then a 360 spin.
    Distinct from Defensive below -- this is the panicked reaction, at
    full power rather than a calm withdrawal."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, BB_SCARED_SPEED, BB_SCARED_SPEED, 1.0)
        d0, d1 = random.choice([(0, 8), (8, 0)])
        await drive_leg(mc, d0, d1, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_FULL)
        print("[EXPRESSIVE]: BB scared retreat complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_angry_charge(droid):
    """BB -- Angry Charge: forward charge then a 360 spin."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, BB_FWD_SPEED, BB_FWD_SPEED, 1.0)
        d0, d1 = random.choice([(0, 8), (8, 0)])
        await drive_leg(mc, d0, d1, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_FULL)
        print("[EXPRESSIVE]: BB angry charge complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_sad_drift(droid):
    """BB -- slow backward drift."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, BB_SLOW_SPEED, BB_SLOW_SPEED, 1.5)
        print("[EXPRESSIVE]: BB sad drift complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_curious_nudge(droid):
    """BB -- small forward nudge."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, BB_SLOW_SPEED, BB_SLOW_SPEED, 0.6)
        print("[EXPRESSIVE]: BB curious nudge complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_defensive_back(droid):
    """BB -- Defensive: calm-power reverse then a 360 spin. Distinct from
    Scared (expressive_bb_retreat) -- this is a measured withdrawal, not a
    panicked one, hence the lower reverse power."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, BB_RETREAT_SPEED, BB_RETREAT_SPEED, 1.0)
        d0, d1 = random.choice([(0, 8), (8, 0)])
        await drive_leg(mc, d0, d1, BB_SPIN_SPEED, BB_SPIN_SPEED, BB_SPIN_FULL)
        print("[EXPRESSIVE]: BB defensive back complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_about_face(droid):
    """BB -- quick 180 then roll away. Spin uses the "Absolute Cold
    Launch" 180 constants (BB_180_SPEED/BB_180_DURATION); direction
    doesn't matter for a 180, so it's randomized each time."""
    mc = droid.motor_controller
    try:
        d0, d1 = random.choice([(0, 8), (8, 0)])
        await drive_leg(mc, d0, d1, BB_180_SPEED, BB_180_SPEED, BB_180_DURATION)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, BB_FWD_SPEED,   BB_FWD_SPEED,  0.7)
        print("[EXPRESSIVE]: BB About Face complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_forward(droid):
    """BB -- come here."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 0, 180, 180, 1.5)
        print("[EXPRESSIVE]: BB forward complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


async def expressive_bb_back(droid):
    """BB -- back up."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 8, 8, 160, 160, 1.5)
        print("[EXPRESSIVE]: BB back complete", flush=True)
    except Exception as e:
        print(f"[EXPRESSIVE ERROR]: {e}", flush=True)


# Full emotion-to-gesture map for BB units -- completely replaces
# EXPRESSIVE_EMOTION_MOVES when DROID_TYPE is "bb", rather than mixing
# BB-power functions into an R-unit-tuned pool.
BB_EXPRESSIVE_EMOTION_MOVES = {
    "happy":     expressive_bb_happy_spin,
    "excited":   expressive_bb_happy_dance,
    "scared":    expressive_bb_retreat,
    "angry":     expressive_bb_angry_charge,
    "sad":       expressive_bb_sad_drift,
    "curious":   expressive_bb_curious_nudge,
    "defensive": expressive_bb_defensive_back,
    "disgusted": expressive_bb_about_face,
}

# chassis appears here, react_to_emotion uses that map exclusively instead
# of EXPRESSIVE_EMOTION_MOVES -- prevents R-unit gestures from leaking into
# the candidate pool for a non-R chassis. GESTURE_VARIANTS (additive
# extras like the BD moonwalk) are skipped for chassis types with a full
# map of their own.
_CHASSIS_MOVE_MAPS = {
    "bb": BB_EXPRESSIVE_EMOTION_MOVES,
}


# Model-specific gesture variations -- extra candidates layered on top of
# the shared R-tuned default for a given (droid_type, emotion) pair. The
# shared default is always included in the pool too; these are additional
# options in the rotation, not replacements. Empty except where a model
# has a unique move of its own. Reuses the existing expressive_moonwalk_bd
# (already present, reachable from the Gestures page's manual
# get_motor_commands() dispatch) rather than defining a second copy.
GESTURE_VARIANTS = {
    ("bd", "excited"): [expressive_moonwalk_bd],
}


async def pet_move(droid):
    """Single randomized movement burst for Pet Entertainer -- fast,
    erratic. R-series only for now."""
    mc = droid.motor_controller
    try:
        move_type = random.choices(
            ["forward", "forward_turn", "turn", "backward"],
            weights=[30, 35, 20, 15],
        )[0]
        duration = random.uniform(1.5, 3.0)
        try:
            if move_type == "forward":
                await drive_leg(mc, 0, 0, 100, 100, duration)
            elif move_type == "forward_turn":
                fwd_time = duration * 0.5
                turn_time = random.uniform(0.2, 0.5)
                await drive_hold(mc, 0, 0, 100, 100, fwd_time)
                turn_dir0, turn_dir1 = (0, 8) if random.random() < 0.5 else (8, 0)
                await drive_hold(mc, turn_dir0, turn_dir1, 100, 100, turn_time)
                await drive_hold(mc, 0, 0, 100, 100, max(0.3, duration - fwd_time - turn_time))
            elif move_type == "turn":
                turn_dir0, turn_dir1 = (0, 8) if random.random() < 0.5 else (8, 0)
                await drive_leg(mc, turn_dir0, turn_dir1, 100, 100, random.uniform(0.3, 0.6))
            elif move_type == "backward":
                await drive_leg(mc, 8, 8, 100, 100, random.uniform(0.5, 1.5))
        finally:
            await stop_motors(mc)
        print(f"[PET]: {move_type} complete", flush=True)
    except Exception as e:
        print(f"[PET ERROR]: {e}", flush=True)


async def pet_move_bb(droid):
    """BB -- pet-entertainment burst using the same V2 pattern as
    hotel_move_bb: two About Face sequences (quick spin + forward) with a
    2s pause. Proven choreography that produces the erratic, unpredictable
    movement pets respond to."""
    mc = droid.motor_controller
    try:
        await drive_leg(mc, 0, 8, BB_QUICK_SPEED, BB_QUICK_SPEED, BB_SPIN_QUICK)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, BB_SLOW_SPEED, BB_SLOW_SPEED, 1.0)
        await asyncio.sleep(2.0)
        await drive_leg(mc, 0, 8, BB_QUICK_SPEED, BB_QUICK_SPEED, BB_SPIN_QUICK)
        await asyncio.sleep(0.2)
        await drive_leg(mc, 0, 0, BB_SLOW_SPEED, BB_SLOW_SPEED, 1.0)
        print("[PET BB]: movement complete", flush=True)
    except Exception as e:
        print(f"[PET BB ERROR]: {e}", flush=True)


async def _pet_burst(droid):
    emotion_map = _load_active_sound_map()
    pet_emotions = [e for e in emotion_map if e not in ("sad", "start up", "blaster", "thruster", "motor")]
    pet_fn = pet_move_bb if get_droid_type() == "bb" else pet_move
    await pet_fn(droid)
    if pet_emotions:
        emotion = random.choice(pet_emotions)
        sounds = emotion_map.get(emotion, [])
        if sounds:
            s = random.choice(sounds)
            try:
                await _play_droid_audio(droid, sound_id=s["sound_id"], bank_id=s["bank_id"])
            except Exception:
                pass


async def _play_confirmation_sound(droid, sounds: list, mode: str, transition: str):
    if not sounds:
        return
    s = random.choice(sounds)
    try:
        await _play_droid_audio(droid, sound_id=s["sound_id"], bank_id=s["bank_id"])
    except Exception as e:
        print(f"[SOUND]: {mode} {transition} confirmation sound failed: {e}", flush=True)


async def _play_mode_change_spin(droid):
    """Visual 360 confirmation for a VOICE-triggered mode change only --
    button presses on the Subroutines page already get an immediate
    visual update in the UI itself, so they deliberately skip this,
    matching Pi's real behavior exactly (see voice_triggered param below)."""
    if get_droid_type() == "bb":
        await expressive_bb_happy_spin(droid)
    else:
        await expressive_happy_spin(droid)


async def _deactivate_all_protocols(droid):
    global _hotel_mode_active, _hotel_end_time, _pet_mode_active, _keepalive_active
    if _hotel_mode_active:
        _hotel_mode_active = False
        _hotel_end_time = 0.0
        print("[PROTOCOL]: Hotel Sentry deactivated", flush=True)
    if _pet_mode_active:
        _pet_mode_active = False
        print("[PROTOCOL]: Pet Entertainer deactivated", flush=True)
    _keepalive_active = False


async def activate_hotel_mode(droid, voice_triggered: bool = False):
    global _hotel_mode_active, _hotel_end_time, _hotel_activated_time, _keepalive_active
    if _hotel_mode_active:
        return
    await _deactivate_all_protocols(droid)
    _hotel_mode_active = True
    _hotel_end_time = time.time() + HOTEL_DURATION
    _hotel_activated_time = time.time()
    _keepalive_active = True
    print("[HOTEL]: Sentry mode activated -- running for 8 hours", flush=True)
    await _play_confirmation_sound(droid, _load_active_sound_map().get("happy", []), "Hotel Sentry", "activation")
    if voice_triggered:
        await _play_mode_change_spin(droid)


async def deactivate_hotel_mode(droid, voice_triggered: bool = False):
    global _hotel_mode_active, _hotel_end_time, _keepalive_active
    _hotel_mode_active = False
    _hotel_end_time = 0.0
    _keepalive_active = False
    print("[HOTEL]: Sentry mode deactivated", flush=True)
    await _play_confirmation_sound(droid, _load_active_sound_map().get("neutral", []), "Hotel Sentry", "deactivation")
    if voice_triggered:
        await _play_mode_change_spin(droid)


async def activate_pet_mode(droid, voice_triggered: bool = False):
    global _pet_mode_active
    if _pet_mode_active:
        return
    await _deactivate_all_protocols(droid)
    _pet_mode_active = True
    print("[PET]: Pet Entertainer activated", flush=True)
    await _play_confirmation_sound(droid, _load_active_sound_map().get("happy", []), "Pet", "activation")
    if voice_triggered:
        await _play_mode_change_spin(droid)


async def deactivate_pet_mode(droid, voice_triggered: bool = False):
    global _pet_mode_active
    _pet_mode_active = False
    print("[PET]: Pet Entertainer deactivated", flush=True)
    await _play_confirmation_sound(droid, _load_active_sound_map().get("neutral", []), "Pet", "deactivation")
    if voice_triggered:
        await _play_mode_change_spin(droid)


async def activate_expressive_mode(droid, voice_triggered: bool = False):
    global _expressive_mode_active
    _expressive_mode_active = True
    print("[EXPRESSIVE]: Expressive mode activated", flush=True)
    await _play_confirmation_sound(droid, _load_active_sound_map().get("excited", _load_active_sound_map().get("happy", [])), "Expressive", "activation")
    if voice_triggered:
        await _play_mode_change_spin(droid)


async def deactivate_expressive_mode(droid, voice_triggered: bool = False):
    global _expressive_mode_active
    _expressive_mode_active = False
    print("[EXPRESSIVE]: Expressive mode deactivated", flush=True)
    await _play_confirmation_sound(droid, _load_active_sound_map().get("neutral", []), "Expressive", "deactivation")
    if voice_triggered:
        await _play_mode_change_spin(droid)


def _phrase_present(phrase: str, lower: str, words: set) -> bool:
    """Multi-word phrases match as a substring (safe -- they don't occur by
    accident). Bare single words must match a WHOLE word, so 'run' fires on
    'run!' but not on 'trunk'/'drunk', and 'away' not on 'hideaway'. Fixes a
    real bug where 'in the trunk' silently triggered the retreat command."""
    if " " in phrase:
        return phrase in lower
    return phrase in words


# Movement gestures are terse, imperative commands ("back up", "come here",
# "run!"). Their trigger phrases are short and common, so they used to fire on
# any sentence that happened to contain them -- "back up, are you telling me
# you're a droid?" and "R2, send for backup" both hit retreat/back even though
# they're plainly conversation. The gate below keeps a movement gesture only
# when the utterance actually looks like a command: not a question, and short.
# Anything longer or ending in '?' falls through to the LLM as an emotion read.
# Mode toggles (hotel/pet/expressive, sleep/wake) are deliberately NOT gated --
# their phrases are distinctive enough that this ambiguity doesn't arise.
_MOVEMENT_GATE_MAX_WORDS = 6


def _looks_imperative(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return False
    return len(t.split()) <= _MOVEMENT_GATE_MAX_WORDS


def check_keywords(text: str) -> str | None:
    """Deterministic phrase matching, checked before Qwen3 ever sees the
    text -- ported near-verbatim from Pi (same phrase lists), minus the
    Autonomous Roam branch (that feature doesn't exist on PC at all now).
    Whisper runs this on every utterance regardless of hotel-mode state
    (it doesn't reliably know that -- see _fetch_ble_status); the hotel
    branch in _handle_transcription is what actually decides whether a
    match gets acted on."""
    lower = text.lower()
    # Whole-word tokens for the single-word triggers below (punctuation stripped).
    words = set(_normalize_for_hallucination(text).split())
    if "stay awake" in lower:  return "stay_awake"
    if "go to sleep" in lower: return "go_to_sleep"
    if "that way" in lower:    return "that_way"

    if any(_phrase_present(p, lower, words) for p in [
        "activate expressive mode", "go into expressive mode", "start expressive mode",
        "little expressive", "get expressive", "move around", "stretch your legs",
        "start expressive", "feel free to roam", "roll around", "go around"
    ]):
        return "expressive_mode_on"
    if any(_phrase_present(p, lower, words) for p in [
        "end expressive mode", "deactivate expressive mode", "stop moving",
        "stand still", "go stationary", "your done"
    ]):
        return "expressive_mode_off"

    if any(_phrase_present(p, lower, words) for p in [
        "keep the air conditioner on", "you're on guard", "you got first watch",
        "activate hotel mode", "start hotel mode", "you're on duty"
    ]):
        return "hotel_mode_on"
    if any(_phrase_present(p, lower, words) for p in [
        "you're off duty", "end hotel mode", "deactivate hotel mode"
    ]):
        return "hotel_mode_off"

    if any(_phrase_present(p, lower, words) for p in [
        "go play with the cats", "go play with the dog", "go play with the others",
        "activate pet mode", "go play"
    ]):
        return "pet_mode_on"
    if any(_phrase_present(p, lower, words) for p in [
        "end pet mode", "deactivate pet mode", "stop pet mode", "stop playing around"
    ]):
        return "pet_mode_off"

    # Movement gestures -- only when the utterance looks like a terse command
    # (see _looks_imperative). Bare one-word "backup" dropped: the movement
    # command is the two-word "back up"; "send for backup" is conversation.
    if _looks_imperative(text):
        if any(_phrase_present(p, lower, words) for p in ["come here", "come to me", "get over here", "move forward"]):
            return "expressive_forward"
        if any(_phrase_present(p, lower, words) for p in ["come back", "don't be like that", "it's okay", "its okay", "sorry about that"]):
            return "expressive_about_face"
        if any(_phrase_present(p, lower, words) for p in ["back up", "move back", "step back", "give me space", "back away", "reverse", "away"]):
            return "expressive_back"
        if any(_phrase_present(p, lower, words) for p in ["hell yeah", "hell yes", "let's go", "lets go", "woohoo", "woo hoo"]):
            return "expressive_dance"
        if any(_phrase_present(p, lower, words) for p in ["look out", "watch out", "run", "get out of there"]):
            return "expressive_retreat"
    return None


async def handle_keyword(droid, keyword: str):
    """Dispatch for a check_keywords() match. R-series gestures only --
    no BB branch, since BB gestures aren't ported on PC yet. Mode-toggle
    keywords pass voice_triggered=True through to their activate/
    deactivate functions, which is what fires the 360 confirmation spin
    (see _play_mode_change_spin) -- button presses on the Subroutines
    page don't, matching Pi's real behavior exactly."""
    global _keepalive_active
    if keyword == "stay_awake":
        _keepalive_active = True
        sound = random.choice(STAY_AWAKE_SOUNDS)
        print("[KEEPALIVE]: Stay awake mode ON", flush=True)
        await _play_droid_audio(droid, sound_id=sound["sound_id"], bank_id=sound["bank_id"])
    elif keyword == "go_to_sleep":
        _keepalive_active = False
        sound = random.choice(GO_TO_SLEEP_SOUNDS)
        print("[KEEPALIVE]: Stay awake mode OFF", flush=True)
        await _play_droid_audio(droid, sound_id=sound["sound_id"], bank_id=sound["bank_id"])
    elif keyword == "that_way":
        print(f"[{DROID_NAME}]: that way -> bank 17, sound 4", flush=True)
        await _play_droid_audio(droid, sound_id=4, bank_id=17)
    elif keyword == "hotel_mode_on":
        await activate_hotel_mode(droid, voice_triggered=True)
    elif keyword == "hotel_mode_off":
        await deactivate_hotel_mode(droid, voice_triggered=True)
    elif keyword == "pet_mode_on":
        await activate_pet_mode(droid, voice_triggered=True)
    elif keyword == "pet_mode_off":
        await deactivate_pet_mode(droid, voice_triggered=True)
    elif keyword == "expressive_mode_on":
        await activate_expressive_mode(droid, voice_triggered=True)
    elif keyword == "expressive_mode_off":
        await deactivate_expressive_mode(droid, voice_triggered=True)
    elif keyword == "expressive_dance":
        if _expressive_mode_active:
            fn = expressive_bb_happy_dance if get_droid_type() == "bb" else expressive_happy_dance
            await fn(droid)
        await react_to_emotion(droid, "excited")
    elif keyword == "expressive_retreat":
        if _expressive_mode_active:
            fn = expressive_bb_retreat if get_droid_type() == "bb" else expressive_retreat
            await fn(droid)
        await react_to_emotion(droid, "scared")
    elif keyword == "expressive_forward":
        if _expressive_mode_active:
            fn = expressive_bb_forward if get_droid_type() == "bb" else expressive_forward
            await fn(droid)
        await react_to_emotion(droid, "happy")
    elif keyword == "expressive_back":
        if _expressive_mode_active:
            fn = expressive_bb_back if get_droid_type() == "bb" else expressive_back
            await fn(droid)
        await react_to_emotion(droid, "neutral")
    elif keyword == "expressive_about_face":
        if _expressive_mode_active:
            fn = expressive_bb_about_face if get_droid_type() == "bb" else expressive_about_face
            await fn(droid)
        await react_to_emotion(droid, "happy")


def keepalive_thread(droid_ref, loop):
    """LED-flash ping sent to the droid every KEEPALIVE_INTERVAL seconds
    while active -- ported from Pi verbatim (same interval, same LED
    command). Runs on its normal cadence throughout Hotel Sentry too, not
    just between moves -- Pi's own comment on this: the droid's real
    auto-sleep timeout is shorter than the 15-minute move interval, so
    skipping the ping during sentry left a dead gap with no signal at all."""
    global _keepalive_active
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        droid = droid_ref[0]
        if droid is None:
            continue
        if _keepalive_active:
            print("[KEEPALIVE]: ping sent", flush=True)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    droid.flash_pairing_led("020001ff01ff01ff00"), loop
                )
                future.result(timeout=5)
            except Exception as e:
                print(f"[KEEPALIVE ERROR]: {e}", flush=True)


def hotel_sentry_thread(droid_ref, loop):
    """Background thread -- moves droid every 15 minutes during Hotel
    Sentry, auto-deactivates after 8 hours. Same cadence as Pi's real
    thread (30s poll interval). Takes droid_ref (a 1-item list) rather than
    a droid directly -- a straight reference captured at thread-start would
    go stale the moment a BLE reconnect swaps in a new DroidConnection
    object, silently dispatching every future move onto a dead connection."""
    global _hotel_mode_active, _hotel_end_time, _hotel_activated_time
    last_move_time = 0.0
    while True:
        time.sleep(30)
        if not _hotel_mode_active:
            last_move_time = 0.0
            continue
        droid = droid_ref[0]
        if droid is None:  # mid-reconnect -- skip this tick, not an error
            continue
        if last_move_time == 0.0:
            last_move_time = _hotel_activated_time
        if time.time() >= _hotel_end_time:
            print("[HOTEL]: 8 hour timer expired -- deactivating sentry", flush=True)
            asyncio.run_coroutine_threadsafe(deactivate_hotel_mode(droid), loop)
            continue
        if time.time() - last_move_time >= HOTEL_MOVE_INTERVAL:
            last_move_time = time.time()
            print("[HOTEL]: Moving to trigger AC sensor", flush=True)
            hotel_fn = hotel_move_bb if get_droid_type() == "bb" else hotel_move
            asyncio.run_coroutine_threadsafe(hotel_fn(droid), loop)


def pet_thread(droid_ref, loop):
    global _pet_mode_active
    while True:
        time.sleep(random.choice(PET_INTERVALS))
        if not _pet_mode_active:
            continue
        droid = droid_ref[0]
        if droid is None:
            continue
        print("[PET]: Starting movement burst", flush=True)
        asyncio.run_coroutine_threadsafe(_pet_burst(droid), loop)


async def discover_droid(mac: str | None = None):
    """Scans for a droid advertising the Disney manufacturer ID. If `mac` is
    given, only a match on that address is accepted -- needed so reconnect
    (and, once claimed, the ordinary connect too) finds the specific paired
    droid rather than grabbing whichever one answers first. `mac=None`
    preserves the original any-droid behavior, still used pre-claim."""
    label = f"droid {mac}" if mac else "any droid"
    print(f"Scanning for {label} (up to {DISCOVERY_TIMEOUT_SECONDS}s)...")
    target = mac.upper() if mac else None
    async with BleakScanner() as scanner:
        deadline = time.time() + DISCOVERY_TIMEOUT_SECONDS
        while time.time() < deadline:
            devices = scanner.discovered_devices_and_advertisement_data
            for addr, (ble_device, adv_data) in devices.items():
                mfr = adv_data.manufacturer_data or {}
                if DisneyBLEManufacturerId.DroidManufacturerId in mfr:
                    if target and ble_device.address.upper() != target:
                        continue
                    print(f"Found a droid: {ble_device.address}")
                    return DroidConnection(ble_device.address, mfr)
            await asyncio.sleep(1)
    raise RuntimeError(f"No {label} found -- is it powered on and in range?")


# ---------------------------------------------------------------------------
# Live status server -- lets the tray/shell (and later, the wizard's Ready
# page) know the brain is actually alive and what it's currently doing,
# without needing a new dependency: plain http.server, same stdlib approach
# kyber_config_server.py already uses, not aiohttp (not in requirements.txt
# and not worth adding just for this). Runs as a background thread inside
# the BLE process specifically, since that's the one process that already
# knows the droid's connection state; mic activity/glitch info arrives from
# the Whisper process over status_queue (a second, dedicated queue -- kept
# separate from emotion_queue so a burst of status updates can never delay
# an actual emotion reaction sitting behind it).
#
# Deliberately NOT modeled on Pi's mapper API (aiohttp, /droid_status,
# fully_ready) -- Pi's version reflects a service that's been running since
# boot; PC's kyber_core.py starts fresh each time the tray/shell launches
# it, so "fully_ready" doesn't mean the same thing here. This is a smaller,
# PC-specific shape: connected / listening / glitched / mic_rms.
# ---------------------------------------------------------------------------

STATUS_PORT = 5010
# Put on emotion_queue when the classifier fails to produce a valid label (a
# glitch), as opposed to the model genuinely returning "confused". The BLE
# reader translates it into a confused reaction AND lights the tray "glitched"
# indicator; a real "confused" reacts without lighting it. Underscored so it can
# never collide with a real (or custom) mood label.
GLITCH_SENTINEL = "__glitch__"
GLITCH_DISPLAY_SECONDS = 4  # how long "glitched" stays true after a glitch
                            # reaction, so the tray icon has time to actually
                            # show it rather than flicker back to idle
STATUS_STALE_SECONDS = 2    # if the Whisper process hasn't reported mic
                            # activity in this long, assume "not listening"
                            # rather than trusting a possibly-stale last value
THINKING_TIMEOUT = 2.0      # bounds the fire-and-forget dome_thinking task,
                            # same reasoning as Pi's own THINKING_TIMEOUT

# ---------------------------------------------------------------------------
# BLE reconnect -- there's no PC equivalent of Pi's 2-hour giveup+shutdown
# (this isn't a headless device), so this retries indefinitely: a fixed
# short interval for the first 10 minutes (most drops are a brief power-cycle
# or range hiccup, worth hammering while it's probably about to come back),
# then exponential backoff to a 60s ceiling so it doesn't spin forever at
# full speed if the droid is actually gone for a while.
# ---------------------------------------------------------------------------
RECONNECT_HEALTH_CHECK_SECONDS = 15  # how often the main loop polls
                                      # droid.droid.is_connected for a silent
                                      # disconnect (power-cycle, walked out
                                      # of range) that never raised an error
RECONNECT_SETTLE_SECONDS       = 30  # skip health checks for this long after
                                      # any (re)connect, so a freshly-opened
                                      # connection isn't flagged before it's
                                      # had a chance to settle
RECONNECT_CONSTANT_SECONDS     = 10 * 60
RECONNECT_CONSTANT_INTERVAL    = 3
RECONNECT_BACKOFF_START        = 3
RECONNECT_BACKOFF_MAX          = 60


async def _connect_droid_with_retries(mac: str | None, silent: bool = False, log_prefix: str = "CONNECT"):
    """Scans for and connects to a droid, retrying forever with the same
    backoff shape as a later disconnect/reconnect (see the constants
    above): a fixed short interval for the first 10 minutes, then
    exponential backoff to a 60s ceiling. Shared by the initial boot
    connect in _ble_main() and _reconnect_droid() below, so "droid isn't
    on yet" behaves identically at startup and mid-session -- this
    replaces what used to be a bare discover_droid() call at boot with
    nothing catching the RuntimeError it raises after a fruitless scan,
    which crashed the whole BLE process outright instead of just waiting."""
    started_at = time.time()
    retry_delay = RECONNECT_BACKOFF_START
    while True:
        try:
            droid = await discover_droid(mac)
            await droid.connect(silent=silent)
            return droid
        except Exception as e:
            if time.time() - started_at < RECONNECT_CONSTANT_SECONDS:
                print(f"[{log_prefix}]  Failed -- {e}. Retrying in {RECONNECT_CONSTANT_INTERVAL}s...", flush=True)
                await asyncio.sleep(RECONNECT_CONSTANT_INTERVAL)
            else:
                print(f"[{log_prefix}]  Failed -- {e}. Retrying in {retry_delay}s...", flush=True)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, RECONNECT_BACKOFF_MAX)


async def _reconnect_droid(old_droid, mac: str | None):
    """Best-effort clear of the dead connection, then hands off to
    _connect_droid_with_retries() for the actual scan/connect retry loop.
    Mirrors Pi's real connect_droid() retry shape, minus the giveup."""
    try:
        await asyncio.wait_for(old_droid.disconnect(), timeout=3)
    except Exception:
        pass
    await asyncio.sleep(2)  # give the OS BLE stack a moment to release the
                            # old connection's state before scanning again

    droid = await _connect_droid_with_retries(mac, log_prefix="RECONNECT")
    print("[RECONNECT]  Droid reconnected!\n", flush=True)
    return droid

_calibration_probe_busy = False  # guards against overlapping probes if the
                                 # Calibration page somehow double-fires a
                                 # request -- same guard Pi's own mapper API
                                 # uses for the identical reason


class _StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            with self.server.status_lock:
                payload = dict(self.server.status_ref)
            # Identity fields the user can change from the Mainframe (Save writes
            # .env). The brain already live-reloads DROID_TYPE for its movement
            # logic (get_droid_type); mirror that here so the Com Uplink dock
            # reflects a model/name change without waiting for a brain restart.
            from dotenv import dotenv_values as _dv
            _fresh = _dv(ENV_PATH)
            payload["droid_type"] = _fresh.get("DROID_TYPE") or DROID_TYPE or "R"
            payload["droid_name"] = _fresh.get("DROID_NAME") or payload.get("droid_name") or ""
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/hotel_status":
            remaining = max(0, int(_hotel_end_time - time.time())) if _hotel_mode_active else 0
            self._reply_json({"active": _hotel_mode_active, "remaining_seconds": remaining})
            return

        if self.path == "/expressive_status":
            self._reply_json({"active": _expressive_mode_active})
            return

        if self.path == "/pet_status":
            self._reply_json({"active": _pet_mode_active})
            return

        self.send_response(404)
        self.end_headers()

    def _reply_json(self, obj: dict, code: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global _calibration_probe_busy, _live_calibration_left, _live_calibration_right
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {}

        if self.path == "/play":
            # Used by sound_discovery_server.py -- this HTTP handler runs in
            # its own thread, not the asyncio loop that actually owns the
            # droid connection, so the real play_audio() call has to be
            # scheduled onto that loop and waited on from here rather than
            # awaited directly.
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected yet"})
                return
            bank_id = data.get("bank_id")
            sound_id = data.get("sound_id")
            try:
                future = asyncio.run_coroutine_threadsafe(
                    droid.audio_controller.play_audio(sound_id=sound_id, bank_id=bank_id), loop
                )
                future.result(timeout=6)
                self._reply_json({"ok": True})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            return

        if self.path == "/mode":
            # While active, the main BLE loop skips react_to_emotion()
            # entirely -- so only the mapper's own explicit /play calls
            # make sound, and normal speech-triggered reactions don't fight
            # with whatever's being tested.
            active = bool(data.get("active", False))
            with self.server.status_lock:
                self.server.status_ref["mapper_active"] = active
            self._reply_json({"ok": True, "mapper_active": active})
            return

        if self.path == "/clear_activation_suppress":
            # Confirmed via a real log: activation_suppress_until (a blind
            # 30s-from-BLE-connect timer) was outlasting the Activation
            # page's actual on-screen animation, so a genuine first "can
            # you hear me?" right after reaching Ready got silently
            # dropped -- suppressed reactions currently -- even though the
            # page had already told the user to go ahead and talk. The
            # timer was only ever a stand-in for "the on-screen story is
            # still playing"; this lets the Activation page's own
            # completion (the real event that actually matters) clear it
            # directly instead of waiting out a guessed duration that
            # doesn't line up with when BLE happened to connect.
            with self.server.status_lock:
                self.server.status_ref["activation_suppress_until"] = 0.0
            self._reply_json({"ok": True})
            return

        if self.path == "/calibration_probe":
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected yet"})
                return
            if _calibration_probe_busy:
                self._reply_json({"ok": False, "reason": "a probe is already running"})
                return
            direction = data.get("direction", "right")
            try:
                scale = float(data.get("scale", 1.0))
            except (TypeError, ValueError):
                self._reply_json({"ok": False, "reason": "invalid scale"})
                return
            _calibration_probe_busy = True
            try:
                future = asyncio.run_coroutine_threadsafe(
                    calibration_spin_probe(droid, direction, scale), loop
                )
                future.result(timeout=15)
                self._reply_json({"ok": True})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            finally:
                _calibration_probe_busy = False
            return

        if self.path == "/calibration_victory":
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected yet"})
                return
            if _calibration_probe_busy:
                self._reply_json({"ok": False, "reason": "a probe is already running"})
                return
            _calibration_probe_busy = True
            try:
                future = asyncio.run_coroutine_threadsafe(calibration_victory(droid), loop)
                future.result(timeout=15)
                self._reply_json({"ok": True})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            finally:
                _calibration_probe_busy = False
            return

        if self.path == "/calibration_set":
            # Overwrites the live in-memory copy directly -- applies to the
            # NEXT gesture immediately, no restart needed. config.py's own
            # CALIBRATION_LEFT_SCALE/RIGHT_SCALE stay whatever they were at
            # this process's launch; kyber_config_server.py is responsible
            # for writing the new values to .env so the NEXT launch picks
            # them up too.
            try:
                left = float(data.get("left_scale", 1.0))
                right = float(data.get("right_scale", 1.0))
            except (TypeError, ValueError):
                self._reply_json({"ok": False, "reason": "invalid scale values"})
                return
            left = max(CALIBRATION_SCALE_MIN, min(CALIBRATION_SCALE_MAX, left))
            right = max(CALIBRATION_SCALE_MIN, min(CALIBRATION_SCALE_MAX, right))
            _live_calibration_left = left
            _live_calibration_right = right
            self._reply_json({"ok": True, "left_scale": left, "right_scale": right})
            return

        if self.path == "/motor_command":
            # Direct scheduling onto the loop, same as /play -- simpler than
            # Pi's queue-plus-polling indirection (a shared list the main
            # loop checks periodically), same functional outcome either way
            # since this process's main loop and this handler both need the
            # same droid connection regardless.
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected"})
                return
            command = data.get("command")
            fn = get_motor_commands().get(command)
            if fn is None:
                self._reply_json({"ok": False, "reason": f"unknown command: {command}"})
                return
            try:
                future = asyncio.run_coroutine_threadsafe(fn(droid), loop)
                future.result(timeout=15)
                self._reply_json({"ok": True, "command": command})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            return

        if self.path == "/motor_test":
            # Raw motor-test primitive for the Motivator panel -- bypasses
            # the named-gesture lookup and drives a single leg directly.
            # No active-protocol guard here (unlike Pi's version) -- would
            # need to check _hotel_mode_active/_pet_mode_active/etc, and
            # a manual motor test conflicting with an active autonomous
            # mode is an edge case worth living with for now rather than
            # adding that guard back in under time pressure.
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected"})
                return
            category = data.get("category")
            directions = MOTOR_TEST_DIRECTIONS.get(category)
            if directions is None:
                self._reply_json({"ok": False, "reason": f"unknown category: {category}"})
                return
            dir0, dir1 = directions
            try:
                duration = float(data.get("duration", 0.5))
                speed0 = int(data.get("speed0", 0))
                speed1 = int(data.get("speed1", 0))
            except (TypeError, ValueError):
                self._reply_json({"ok": False, "reason": "invalid duration/speed values"})
                return
            duration = max(MOTOR_TEST_DURATION_MIN, min(MOTOR_TEST_DURATION_MAX, duration))
            speed0 = max(0, min(255, speed0))
            speed1 = max(0, min(255, speed1))
            try:
                future = asyncio.run_coroutine_threadsafe(
                    drive_leg(droid.motor_controller, dir0, dir1, speed0, speed1, duration), loop
                )
                future.result(timeout=15)
                self._reply_json({"ok": True, "category": category, "duration": duration, "speed0": speed0, "speed1": speed1})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            return

        if self.path == "/hotel_toggle":
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected"})
                return
            active = bool(data.get("active", False))
            try:
                coro = activate_hotel_mode(droid) if active else deactivate_hotel_mode(droid)
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                future.result(timeout=10)
                self._reply_json({"ok": True, "active": active})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            return

        if self.path == "/expressive_toggle":
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected"})
                return
            active = bool(data.get("active", False))
            try:
                coro = activate_expressive_mode(droid) if active else deactivate_expressive_mode(droid)
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                future.result(timeout=10)
                self._reply_json({"ok": True, "active": active})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            return

        if self.path == "/pet_toggle":
            droid = self.server.droid_ref[0]
            loop = self.server.loop_ref
            if droid is None or loop is None:
                self._reply_json({"ok": False, "reason": "droid not connected"})
                return
            active = bool(data.get("active", False))
            try:
                coro = activate_pet_mode(droid) if active else deactivate_pet_mode(droid)
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                future.result(timeout=10)
                self._reply_json({"ok": True, "active": active})
            except Exception as e:
                self._reply_json({"ok": False, "reason": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # quiet -- this gets polled multiple times a second by the tray


def start_status_server(status_ref: dict, status_lock: threading.Lock, droid=None, loop=None):
    """Runs forever in a daemon thread. status_ref is a plain dict the BLE
    process's main loop mutates directly (under status_lock) -- no queue
    needed for this side since both the server thread and the loop that
    updates it live in the same process. droid/loop are needed only for
    /play -- the mapper's way of triggering real sound playback through
    the brain's live connection."""
    server = ThreadingHTTPServer(("127.0.0.1", STATUS_PORT), _StatusHandler)
    server.status_ref = status_ref
    server.status_lock = status_lock
    server.droid_ref = droid
    server.loop_ref = loop
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Process 1: BLE / droid / Beacon Relay
# ---------------------------------------------------------------------------

async def _ble_main(emotion_queue, status_queue, keyword_queue):

    def _ts():
        return time.strftime("%H:%M:%S")

    # Same one-shot pattern Pi's real kyber_core.py uses: a flag set right
    # before this process is launched (by the Activation page, via
    # kyber_config_server.py), read once here, and cleared immediately so
    # it can never replay on a later restart. silent=True suppresses the
    # library's own automatic "paired!" chirp+light cue for this one
    # connect -- otherwise it'd clash with Disney's own activation show
    # about to play right after.
    from dotenv import dotenv_values as _dv, set_key as _set_key
    play_activation = _dv(ENV_PATH).get("PLAY_ACTIVATION_ON_NEXT_BOOT") == "1"

    # Real bug fixed here: this used to be a bare discover_droid() call --
    # no retry, nothing catching the RuntimeError it raises after a
    # fruitless scan -- so starting up with the droid simply not powered on
    # yet crashed this entire process outright (confirmed from a real run:
    # Whisper/tray/Mainframe all kept going, but BLE died for good with no
    # way back short of a full relaunch). _connect_droid_with_retries() is
    # the same retry-forever-with-backoff shape already used for a later
    # mid-session disconnect (see _reconnect_droid) -- "droid isn't on yet"
    # now behaves the same at boot as it already did mid-session.
    print(f"[{_ts()}] Connecting to droid...")
    droid = await _connect_droid_with_retries(DROID_MAC or None, silent=play_activation, log_prefix="CONNECT")
    print(f"[{_ts()}] Droid connected!\n")

    if play_activation:
        _set_key(ENV_PATH, "PLAY_ACTIVATION_ON_NEXT_BOOT", "0")
        try:
            await DroidScriptEngine(droid).execute_script(DroidScripts.DroidBayActivationSequence)
            print(f"[{_ts()}] [ACTIVATION]: Triggered DroidBayActivationSequence over BLE", flush=True)
        except Exception as e:
            print(f"[{_ts()}] [ACTIVATION]: Failed to trigger activation sequence -- {e}", flush=True)

    # Shared with the status HTTP server thread -- lock-guarded since two
    # threads (this asyncio loop's thread, and the server's request-handling
    # threads) touch it. droid_mac is static for the life of this process;
    # droid_name/droid_type are seeded here but re-read fresh from .env in the
    # /status handler (the Com Uplink dock polls it), so a model or name change
    # saved from the Mainframe shows up without a brain restart -- matching how
    # get_droid_type() already live-reloads the model for the movement logic.
    status_ref = {
        "connected": True, "listening": False, "glitched": False, "mic_rms": 0,
        "mapper_active": False, "activation_suppress_until": 0.0, "hotel_mode_active": False,
        "mic_ready": False, "mic_gate_until": 0.0,
        "droid_mac": getattr(droid, "profile", DROID_MAC or ""),
        "droid_name": DROID_NAME,
        "droid_type": DROID_TYPE,
        # Live Com Uplink feed (v0.86.1): the mood the brain last chose, the
        # text that triggered it, a rolling log of recent heard/emotion pairs,
        # and when the last reaction fired. Populated from the Whisper process
        # via status_queue (see the emotion branch in the reader below).
        "emotion": None, "heard": "", "recent": [], "last_reaction_ts": 0.0,
    }
    if play_activation:
        # ~30s from here comfortably outlasts the remaining on-screen
        # loading animation (37s total, but the page delays firing 15s in,
        # so by the time this connect finishes only ~15-20s have usually
        # elapsed) -- covers the real bug: kyber_core.py is fully able to
        # react to ambient speech within seconds of connecting, well
        # before the Activation page's own narrative has finished telling
        # the story that the droid is "coming online for the first time."
        status_ref["activation_suppress_until"] = time.time() + 30
    status_lock = threading.Lock()
    loop = asyncio.get_running_loop()

    # A 1-item list, not a plain variable -- this is the one object the
    # status server thread and the hotel/pet threads share with this
    # loop. When a reconnect swaps in a new DroidConnection, updating
    # droid_ref[0] here is what lets all four other consumers see the new
    # connection instead of quietly dispatching onto the dead one.
    droid_ref = [droid]
    start_status_server(status_ref, status_lock, droid=droid_ref, loop=loop)
    print(f"[{_ts()}] [BLE PROCESS] Status server running at http://127.0.0.1:{STATUS_PORT}/status")

    threading.Thread(target=hotel_sentry_thread, args=(droid_ref, loop), daemon=True).start()
    threading.Thread(target=pet_thread, args=(droid_ref, loop), daemon=True).start()
    threading.Thread(target=keepalive_thread, args=(droid_ref, loop), daemon=True).start()

    last_glitch_time = 0.0
    last_mic_update_time = 0.0
    last_health_check = time.time()
    last_reconnect_time = time.time()  # prevents a health check from firing
                                        # immediately on boot -- see
                                        # RECONNECT_SETTLE_SECONDS below

    publisher = None
    watcher = None

    if BEACON_RELAY_ENABLED:
        advertisement = build_advertisement(droid_paired=True)

        publisher = BluetoothLEAdvertisementPublisher(advertisement)
        publisher.add_status_changed(on_publisher_status_changed)

        watcher = BluetoothLEAdvertisementWatcher()
        watcher.add_received(on_advertisement_received)

        print("Starting publisher (broadcasting droid's presence)...")
        publisher.start()

        print("Starting watcher (scanning for nearby beacons)...")
        watcher.start()

        print("\n[BLE PROCESS] Everything running: connected + broadcasting + scanning.")
    else:
        print("\n[BLE PROCESS] Beacon Relay disabled (BEACON_RELAY_ENABLED=false) -- connected only.")

    print("[BLE PROCESS] Waiting for classified emotions from the Whisper/Qwen3 process.")
    print("Ctrl+C to stop.\n")

    try:
        while True:
            try:
                emotion = emotion_queue.get_nowait()
                with status_lock:
                    mapper_active = status_ref.get("mapper_active", False)
                    suppressed = time.time() < status_ref.get("activation_suppress_until", 0.0)
                if not mapper_active and not suppressed and not _hotel_mode_active:
                    if emotion == GLITCH_SENTINEL:
                        # A classifier glitch: light the tray indicator and play
                        # the confused reaction. A REAL "confused" label skips
                        # this branch, so it reacts without the glitch light.
                        last_glitch_time = time.time()
                        emotion = "confused"
                    await react_to_emotion(droid, emotion)
            except queue.Empty:
                pass

            try:
                keyword = keyword_queue.get_nowait()
                with status_lock:
                    mapper_active = status_ref.get("mapper_active", False)
                    suppressed = time.time() < status_ref.get("activation_suppress_until", 0.0)
                # No extra hotel-mode check here -- the Whisper side already
                # only lets "hotel_mode_off" through while sentry is active
                # (see _handle_transcription), so anything that reaches this
                # queue during hotel mode is already the one keyword meant
                # to get through.
                if not mapper_active and not suppressed:
                    await handle_keyword(droid, keyword)
            except queue.Empty:
                pass

            # Drain every status update currently queued rather than just
            # one per tick, so a brief burst from the Whisper process (it
            # pushes roughly as often as it processes audio chunks) can
            # never back up -- only the most recent one actually matters.
            while True:
                try:
                    update = status_queue.get_nowait()
                except queue.Empty:
                    break
                # Com Uplink feed (v0.86.1): emotion updates carry the heard
                # text + the mood that fired. Record them into the live status
                # and the rolling recent[] log, then skip the mic-status
                # handling below -- these carry no rms/speaking payload, so
                # they must not disturb the "listening" / stale-mic logic.
                if "gesture" in update:
                    # A voice command the droid acted on. Logged like an
                    # emotion but tagged as a gesture so the UI renders it as
                    # an action, not a feeling. Does NOT touch status_ref
                    # ["emotion"] -- the live feeling bubble stays on the last
                    # real mood.
                    with status_lock:
                        status_ref["heard"] = update.get("heard", "")
                        status_ref["last_reaction_ts"] = time.time()
                        recent = status_ref.get("recent", [])
                        recent.append({
                            "heard": update.get("heard", ""),
                            "gesture": update["gesture"],
                            "ts": time.time(),
                        })
                        status_ref["recent"] = recent[-8:]
                    continue
                if "emotion" in update:
                    with status_lock:
                        status_ref["emotion"] = update["emotion"]
                        status_ref["heard"] = update.get("heard", "")
                        status_ref["last_reaction_ts"] = time.time()
                        recent = status_ref.get("recent", [])
                        recent.append({
                            "heard": update.get("heard", ""),
                            "emotion": update["emotion"],
                            "ts": time.time(),
                        })
                        status_ref["recent"] = recent[-8:]
                    continue
                last_mic_update_time = time.time()
                with status_lock:
                    status_ref["mic_rms"] = update.get("rms", status_ref["mic_rms"])
                    status_ref["listening"] = bool(update.get("speaking", False))
                    if update.get("mic_ready"):
                        status_ref["mic_ready"] = True
                    mapper_active = status_ref.get("mapper_active", False)
                    suppressed = time.time() < status_ref.get("activation_suppress_until", 0.0)
                # "Thinking" signal from the Whisper process -- fired the
                # moment a complete speech segment is captured, before
                # transcription even starts, so this covers the full
                # STT+LLM latency window rather than just the LLM portion.
                # Same guards react_to_emotion() already gets, so Hotel
                # Sentry/the Sound Mapper/the post-activation window don't
                # get an uninvited head twitch.
                if update.get("thinking") and not _hotel_mode_active and not mapper_active and not suppressed:
                    async def _safe_thinking_anim():
                        try:
                            await asyncio.wait_for(dome_thinking(droid.motor_controller), timeout=THINKING_TIMEOUT)
                        except Exception:
                            pass
                    asyncio.create_task(_safe_thinking_anim())

            now = time.time()
            stale = (now - last_mic_update_time) > STATUS_STALE_SECONDS
            with status_lock:
                if stale:
                    status_ref["listening"] = False
                status_ref["glitched"] = (now - last_glitch_time) < GLITCH_DISPLAY_SECONDS
                # Whisper runs in a separate process and has no other way to
                # see this -- it polls /status before deciding whether a
                # transcribed utterance should be treated as Hotel Sentry's
                # "ignore everything but the stop phrase" case.
                status_ref["hotel_mode_active"] = _hotel_mode_active
                # Same idea, for the mic gate against R2 hearing his own
                # noise -- Whisper checks this the moment a segment
                # finalizes, before firing the thinking animation or
                # burning a transcription on what's likely just his own
                # motor/sound noise.
                status_ref["mic_gate_until"] = _mic_gate_until

            # Periodic BLE health check -- catches a silent disconnect (the
            # droid was power-cycled, or walked out of range) that never
            # raised an exception anywhere, since nothing was actively being
            # sent to it at the time. Skipped for a settle window right
            # after any (re)connect so a freshly-opened connection isn't
            # immediately flagged as dropped.
            if (now - last_health_check > RECONNECT_HEALTH_CHECK_SECONDS
                    and now - last_reconnect_time > RECONNECT_SETTLE_SECONDS):
                last_health_check = now
                still_connected = True
                try:
                    if hasattr(droid, "is_connected"):
                        still_connected = droid.is_connected
                    elif hasattr(droid, "droid") and hasattr(droid.droid, "is_connected"):
                        still_connected = droid.droid.is_connected
                except Exception:
                    still_connected = True  # a failed check isn't proof of
                                             # disconnect -- don't reconnect
                                             # on a fluke
                if not still_connected:
                    print("[BLE]  Health check detected a disconnect -- reconnecting...", flush=True)
                    with status_lock:
                        status_ref["connected"] = False
                    droid_ref[0] = None  # status-server handlers now report
                                         # "droid not connected" instead of
                                         # trying a GATT write on a dead link
                    droid = await _reconnect_droid(droid, DROID_MAC or None)
                    droid_ref[0] = droid
                    last_reconnect_time = time.time()
                    with status_lock:
                        status_ref["connected"] = True

            await asyncio.sleep(0.1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[BLE PROCESS] Stopping...")
        with status_lock:
            status_ref["connected"] = False
        if watcher is not None:
            watcher.stop()
        if publisher is not None:
            publisher.stop()
        await droid.disconnect()
        print("[BLE PROCESS] Stopped cleanly.")


def run_ble_process(emotion_queue, status_queue, keyword_queue):
    _import_ble_libs()
    try:
        asyncio.run(_ble_main(emotion_queue, status_queue, keyword_queue))
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Process 2: Whisper / Qwen3
# ---------------------------------------------------------------------------

conversation_history = deque(maxlen=HISTORY_LENGTH)

# ---------------------------------------------------------------------------
# Emotional momentum -- a SOFT prior, never an override.
# ---------------------------------------------------------------------------
# A charged feeling lingers for a beat when what follows is calm or ambiguous,
# but ANY clear new feeling wins instantly: the momentum is injected into the
# prompt as a line that explicitly yields (see _momentum_line + the guidance in
# _build_system_prompt), and the LLM decides. The timer below only governs how
# long an UNREINFORCED feeling hangs on -- it can never force a stale mood over a
# real new one. Only high-arousal moods linger; neutral/curious/confused never
# do (absent from the map -> 0), since those are exactly the low-charge states
# we don't want persisting. A mood sustained ONLY by momentum (it was injected
# and the model echoed it) burns the timer DOWN rather than refreshing, so it
# fades on its own instead of looping.
_MOMENTUM_LINGER = {
    "scared": 2, "angry": 2, "defensive": 2,   # high-arousal -- linger longest
    "sad": 1, "disgusted": 1, "excited": 1, "happy": 1,
}
_momentum_emotion = None
_momentum_ttl = 0


def _momentum_line() -> str:
    """The soft-prior prompt line for the feeling still lingering from last
    turn, or '' when nothing is lingering. Firmness scales with freshness; the
    line ALWAYS yields to a clear new feeling."""
    if _momentum_ttl <= 0 or not _momentum_emotion:
        return ""
    if _momentum_ttl >= 2:
        lead = f"You are still feeling {_momentum_emotion} from a moment ago."
    else:
        lead = f"A trace of {_momentum_emotion} still lingers from a moment ago."
    return ("\n" + lead + " If this new line clearly calls for a different "
            "feeling, feel THAT instead -- a real new reaction always wins. Only "
            f"stay {_momentum_emotion} if the new line is calm, ambiguous, or "
            "nothing has changed.\n")


def _update_momentum(new_emotion: str, was_injected: bool) -> None:
    """Advance momentum after a successful classification.

    A genuinely NEW feeling (differs from the lingering one) resets the timer to
    that feeling's linger budget. The SAME feeling sustained only by momentum
    (momentum was injected AND the model echoed it) burns the timer DOWN instead
    of refreshing, so an unreinforced mood fades rather than looping forever. A
    same-feeling read with NO momentum injected (a fresh, self-standing signal)
    sets the timer normally. Glitches don't call this at all -- a non-read never
    changes how the droid feels."""
    global _momentum_emotion, _momentum_ttl
    if new_emotion == _momentum_emotion and was_injected:
        _momentum_ttl -= 1
    else:
        _momentum_emotion = new_emotion
        _momentum_ttl = _MOMENTUM_LINGER.get(new_emotion, 0)


def _fetch_ble_status() -> dict:
    """Whisper runs in a separate process from the BLE connection that
    actually tracks all of this -- one quick, best-effort request against
    the status server, reused for every check below (suppression windows
    AND hotel-mode state) instead of a separate round trip for each. Empty
    dict on any failure, and every caller below treats a missing key as its
    own safe default, so a brief network blip can't accidentally suppress
    normal conversation or block a hotel-mode stop command."""
    try:
        r = requests.get(f"http://127.0.0.1:{STATUS_PORT}/status", timeout=0.5)
        return r.json()
    except Exception:
        return {}


def _push_gesture(status_queue, text, keyword):
    """Hand the BLE process a gesture event (heard text + which command fired)
    for the Com Uplink log. Additive -- never break the command path."""
    try:
        status_queue.put({"heard": text, "gesture": keyword})
    except Exception:
        pass


def _handle_transcription(text: str, emotion_queue, keyword_queue, status_queue):
    """Instead of scheduling a droid reaction directly (no droid connection
    exists in this process at all), this pushes onto one of the two shared
    queues -- the BLE process picks it up and does the actual reacting.

    Keyword phrases are checked before Qwen3 ever sees the text, same as
    Pi -- and during Hotel Sentry, mirrors Pi's real behavior exactly:
    sentry mode only ever listens for its own stop phrase while active,
    ignoring everything else (including ordinary conversation) so it stays
    inert except for its own scheduled move. That's also why hotel mode
    is checked here rather than left to the BLE side alone: skipping the
    LLM call entirely for nothing while sentry is running, not just
    discarding its result downstream once it's already been paid for."""
    if not text:
        return

    status = _fetch_ble_status()
    if status.get("mapper_active", False) or time.time() < status.get("activation_suppress_until", 0.0):
        print(f'[DICTATION] "{text}" (skipped -- reactions currently suppressed)')
        return

    print(f'[DICTATION] "{text}"')
    keyword = check_keywords(text)

    if status.get("hotel_mode_active", False):
        if keyword == "hotel_mode_off":
            print("[HOTEL]: Stop command received", flush=True)
            keyword_queue.put(keyword)
            _push_gesture(status_queue, text, keyword)  # log the acted-on command
        else:
            print("[HOTEL]: Sentry active -- ignoring", flush=True)
        return

    if keyword:
        print(f"[KEYWORD]  {keyword}", flush=True)
        keyword_queue.put(keyword)
        # Com Uplink feed (v0.86.1): a voice command is an action, not an
        # emotion -- log it so the chat shows what was heard and what the droid
        # DID, distinct from the emotional reactions.
        _push_gesture(status_queue, text, keyword)
        return

    # Momentum: capture the lingering feeling BEFORE classifying (so the model
    # sees it), and remember whether it was actually injected -- _update_momentum
    # needs that to tell a momentum-sustained repeat from a fresh self-standing
    # read. A glitch (emotion is None) leaves momentum untouched.
    mo_line = _momentum_line()
    was_injected = bool(mo_line)
    emotion = get_emotion_test(text, conversation_history, mo_line)

    if emotion is None:
        print(f'[REACTION]  *glitched* -- "{DROID_NAME} glitched. Please try again."\n')
        # A glitch (the classifier couldn't produce a valid label) is NOT the
        # same as the droid genuinely feeling "confused". Send a dedicated
        # sentinel so the BLE side lights the tray "glitched" indicator and
        # plays the confused reaction, while a REAL "confused" classification
        # (below) drives the reaction WITHOUT tripping the glitch light.
        emotion_queue.put(GLITCH_SENTINEL)
        fired = "confused"
    else:
        print(f"[REACTION]  {DROID_NAME} feels: {emotion}\n")
        conversation_history.append((text, emotion))
        _update_momentum(emotion, was_injected)
        emotion_queue.put(emotion)
        fired = emotion

    # Com Uplink feed (v0.86.1): hand the BLE process what was heard and the
    # mood that fired so /status can surface it live. Additive -- must never
    # break the reaction path if the queue misbehaves.
    try:
        status_queue.put({"heard": text, "emotion": fired})
    except Exception:
        pass


def _whisper_main(emotion_queue, status_queue, keyword_queue, model):
    global _ambient_rms_est, _last_logged_floor
    print(f"\n{DROID_NAME} is listening... (dynamic ambient floor starting at {VAD_RMS_FLOOR_MIN} -- watch the live RMS number and adjust VAD_RMS_* if needed)")

    buffer = []
    is_speaking = False
    silence_start = None
    loud_since = None
    consecutive_below_noise_floor = 0
    pre_roll = deque(maxlen=PRE_ROLL_CHUNKS)
    consecutive_above = 0
    last_status_print = 0.0
    last_status_push = 0.0
    STATUS_PUSH_INTERVAL = 0.15  # throttled independently of the print
                                 # cadence below -- this is what the tray
                                 # icon/Ready page actually poll, so it needs
                                 # to feel live without flooding status_queue

    # The real fix for "disconnecting mic and reconnecting never re-enables
    # it": audio_queue.get() used to block forever with no timeout. If the
    # OS-level device disappears, sounddevice's callback just stops firing
    # -- no exception, no crash, nothing -- so that blocking .get() would
    # hang indefinitely and NOTHING would ever notice the mic came back.
    # This now uses a timeout, and if no audio has arrived for a while,
    # closes and reopens the stream fresh.
    #
    # That alone wasn't enough, though (confirmed still broken): a plain
    # sd.InputStream() reopen still bound to a dead device and produced
    # nothing but rms=0 forever after. PortAudio enumerates devices once
    # when it's first initialized and doesn't notice a device coming back
    # on its own -- sd._terminate()/_initialize() forces a real re-scan,
    # which is the actual fix, not just retrying the same stale open. A
    # short settle delay first gives Windows a moment to finish
    # re-registering the device before we try to bind to it.
    STREAM_STALL_SECONDS = 5.0

    # Second, distinct problem from the one above (confirmed from a real
    # run: droid+mic both deliberately left off at boot, then connected
    # later): if the intended mic simply isn't the WASAPI default yet the
    # moment this process starts, _open_stream() below still succeeds --
    # it just binds to whatever WAS the default at that instant (built-in
    # laptop mic, whatever). That device keeps producing real callbacks,
    # so the silence-stall check above never fires -- it only notices
    # audio stopping entirely, not audio quietly arriving from the wrong
    # device. Connecting the intended mic afterward changes what Windows
    # reports as the default, but PortAudio streams don't auto-follow a
    # default-device change once opened, so nothing ever revisits that
    # first, wrong choice on its own.
    #
    # A genuinely live mic's RMS is essentially never exactly zero (room
    # noise, the mic's own self-noise) even in a quiet room -- unlike a
    # disconnected/muted/virtual device, which produces true digital
    # silence. So a sustained run of near-zero RMS despite audio actually
    # arriving is treated as "probably the wrong device" and triggers the
    # same reopen-with-refresh path as a real stall. Worth knowing: a
    # Bluetooth headset using DTX (discontinuous transmission, which some
    # send literal silence during pauses to save bandwidth) could in
    # theory trip this during a long quiet stretch on a mic that's
    # actually fine -- if that turns out to happen in practice, raise
    # DEAD_DEVICE_RMS_EPSILON or DEAD_DEVICE_SILENCE_SECONDS rather than
    # removing the check.
    DEAD_DEVICE_RMS_EPSILON = 1.0
    DEAD_DEVICE_SILENCE_SECONDS = 10.0
    zero_rms_since = None

    # Third problem, reported directly from a real test: sustained loud
    # ambient noise (a vacuum next to the mic, a noisy theme park, a
    # nightclub) can sit above the dynamic floor continuously, so
    # silence_start below never gets set and an utterance would otherwise
    # never close on its own. Tiered rather than a single flat cap,
    # confirmed via back-and-forth on real numbers:
    #   - at or below SUSTAINED_NOISE_FLOOR: today's behavior, untouched,
    #     no cap at all -- a long, uninterrupted conversation in a quiet
    #     room is allowed to run as long as it likes.
    #   - above that floor but at/below SUSTAINED_NOISE_LOUD_RMS (roughly
    #     "busy theme park" territory): capped at
    #     SUSTAINED_NOISE_CAP_MODERATE seconds.
    #   - above SUSTAINED_NOISE_LOUD_RMS (roughly "nightclub" territory,
    #     where real exchanges are short shouted sentences, not
    #     monologues): capped tighter, at SUSTAINED_NOISE_CAP_LOUD seconds.
    # loud_since tracks one continuous streak -- resets once RMS dips back
    # to the quiet floor or below for SUSTAINED_NOISE_DROP_CHUNKS in a row
    # (not instantly on a single chunk -- confirmed from a real run that a
    # single-chunk reset was too fragile: real noise has natural texture,
    # a vacuum or crowd never sits at a perfectly flat level, so one brief
    # dip at/below the floor could wipe an otherwise-genuine sustained
    # streak before it ever reached the cap. Same debounce idea already
    # used for confirming speech has actually started, just applied to
    # the reset side here instead). This only fires on genuinely sustained
    # noise, never on ordinary speech (which dips below SUSTAINED_NOISE_FLOOR
    # constantly between words, and for much longer than a handful of
    # chunks at a time). The cap that applies is re-evaluated every chunk
    # against the *current* RMS, not locked in at the start of the streak
    # -- so if noise escalates mid-utterance from moderate into loud
    # territory, the tighter cap takes over from that point rather than
    # waiting out the looser one.
    # This is a forced CLOSE, not a hold -- the line has been open and
    # accumulating the whole time either way; this just ends it early,
    # the same as a natural pause would, instead of never ending at all.
    SUSTAINED_NOISE_FLOOR = 1200.0
    SUSTAINED_NOISE_LOUD_RMS = 2800.0
    SUSTAINED_NOISE_CAP_MODERATE = 15.0
    SUSTAINED_NOISE_CAP_LOUD = 10.0
    SUSTAINED_NOISE_DROP_CHUNKS = 10  # ~300ms at 30ms/chunk

    def _finalize_utterance(reason: str):
        """Shared by the normal silence-hang finalize and the sustained-
        noise forced-close above it -- same steps either way: cut the
        segment, reset per-utterance state, transcribe. `reason` only
        affects the console print, so it's clear afterward *why* this
        particular capture ended when it did."""
        nonlocal buffer, is_speaking, silence_start, consecutive_above
        segment = np.concatenate(buffer).flatten()
        duration = len(segment) / SAMPLE_RATE
        buffer = []
        is_speaking = False
        silence_start = None
        consecutive_above = 0

        if duration < MIN_SPEECH_SECONDS:
            print(f"\r[skipped]  blip too short ({duration:.2f}s)".ljust(60))
            return

        # Discard before the thinking animation or a real transcription
        # fires -- this is very likely R2 hearing his own motor/sound
        # noise from whatever he just did, not a real utterance (see
        # _stamp_mic_gate and its call sites). Same reasoning and same
        # place in the sequence as Pi's real _mic_gate_until check.
        status = _fetch_ble_status()
        if time.time() < status.get("mic_gate_until", 0.0):
            print("~", end="", flush=True)
            _drain_audio_backlog()
            return

        # Fire the dome "thinking" sweep now, before transcription even
        # starts -- covers the full STT+LLM latency window (roughly 1-2s)
        # rather than just the LLM portion, so R2 appears to react the
        # instant speech ends. Reuses status_queue (already flowing this
        # direction) instead of adding a whole new multiprocessing.Queue
        # for one flag.
        status_queue.put({"rms": rms, "speaking": False, "thinking": True})

        print(f"\r[transcribing]  ({duration:.1f}s of audio, {reason})...".ljust(60))
        text, elapsed = transcribe_segment(model, segment)
        print(f"[WHISPER]  {elapsed:.2f}s" + (f'  "{text}"' if text else "  (nothing recognized)"))
        _handle_transcription(text, emotion_queue, keyword_queue, status_queue)
        # Whatever accumulated in audio_queue while transcribe_segment()/
        # _handle_transcription() were blocking (LLM call included) is
        # stale by now -- discard it so the next listen starts from
        # silence, not a backlog of noise from during the reaction that's
        # about to play.
        _drain_audio_backlog()

    def _open_stream(refresh_devices: bool = False):
        if refresh_devices:
            try:
                sd._terminate()
            except Exception:
                pass
            try:
                sd._initialize()
            except Exception as e:
                print(f"[MIC] Could not refresh audio device list: {e}", flush=True)
        device = _resolve_wasapi_input_device()
        # auto_convert=True -- explicit WASAPI targeting exposed a real
        # crash: unlike the legacy MME path it replaced, WASAPI doesn't
        # silently resample a requested rate that doesn't match a device's
        # native one, it just raises "Invalid sample rate". This tells it
        # to do the conversion anyway, same as MME always did.
        extra_settings = sd.WasapiSettings(auto_convert=True) if device is not None else None
        s = sd.InputStream(device=device, samplerate=SAMPLE_RATE, channels=1, dtype='int16',
                            blocksize=BLOCK_SIZE, callback=audio_callback,
                            extra_settings=extra_settings)
        s.start()
        return s

    def _reopen_stream(reason: str):
        """Shared by the silence-stall path and the dead/wrong-device path
        below -- same recovery either way: drop whatever was mid-capture
        (it's corrupted -- spliced with dead air or wrong-device noise),
        force a real device rescan, and rebind."""
        nonlocal stream, buffer, is_speaking, silence_start, consecutive_above
        print(f"[MIC] {reason} -- reopening input stream "
              f"(mic may have been disconnected/reconnected)", flush=True)
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        # Second half of the original stall bug: even a perfect reopen
        # still left a stale is_speaking/buffer around, so the very next
        # real audio just fed into a poisoned buffer.
        buffer = []
        is_speaking = False
        silence_start = None
        pre_roll.clear()
        consecutive_above = 0
        time.sleep(1.0)  # let the OS finish re-registering the device
                          # before binding to it again
        try:
            stream = _open_stream(refresh_devices=True)
            print("[MIC] Input stream reopened.", flush=True)
        except Exception as e:
            print(f"[MIC] Could not reopen input stream: {e}", flush=True)

    # Covers a totally mic-less boot too (no input device at all yet, not
    # just "the wrong one") with the same retry-forever shape as the BLE
    # side's droid connect, rather than letting an unhandled exception
    # here take down the whole Whisper process the way the BLE process
    # used to crash outright on a missing droid.
    stream = None
    while stream is None:
        try:
            stream = _open_stream()
        except Exception as e:
            print(f"[MIC] Could not open input stream -- {e}. Retrying in 3s...", flush=True)
            time.sleep(3.0)
    last_audio_time = time.time()

    # Distinct from BLE's own "connected" status -- that only reflects the
    # droid's BLE link, which has nothing to do with whether THIS process
    # (model load + mic stream) has actually finished starting up. The
    # Ready page was treating BLE-connected as "ready to talk," but
    # Whisper loading its model is frequently the slower of the two --
    # this is the real signal for "can actually hear you now."
    status_queue.put({"rms": 0, "speaking": False, "mic_ready": True})

    try:
        while True:
            try:
                chunk = audio_queue.get(timeout=1.0)
                last_audio_time = time.time()
            except queue.Empty:
                if time.time() - last_audio_time > STREAM_STALL_SECONDS:
                    _reopen_stream(f"No audio in {STREAM_STALL_SECONDS:.0f}s")
                    last_audio_time = time.time()  # reset so a failed reopen
                                                    # doesn't spam-retry every
                                                    # single second
                    zero_rms_since = None
                continue

            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

            if rms < DEAD_DEVICE_RMS_EPSILON:
                if zero_rms_since is None:
                    zero_rms_since = time.time()
                elif time.time() - zero_rms_since > DEAD_DEVICE_SILENCE_SECONDS:
                    _reopen_stream(f"Suspiciously exact silence for {DEAD_DEVICE_SILENCE_SECONDS:.0f}s "
                                   f"(likely bound to the wrong or a dead device)")
                    last_audio_time = time.time()
                    zero_rms_since = None
                    continue
            else:
                zero_rms_since = None

            dynamic_floor = min(
                VAD_RMS_FLOOR_MAX,
                max(VAD_RMS_FLOOR_MIN, _ambient_rms_est + VAD_RMS_MARGIN),
            )
            if abs(dynamic_floor - _last_logged_floor) >= AMBIENT_LOG_DELTA:
                print(f"\n[AMBIENT]: floor {_last_logged_floor:.0f} -> {dynamic_floor:.0f} "
                      f"(ambient_est={_ambient_rms_est:.0f})", flush=True)
                _last_logged_floor = dynamic_floor

            if is_speaking:
                # Already confirmed as real speech -- keep buffering,
                # extend on continued sound, finalize after the hang timer
                # (or the sustained-noise cap below, whichever comes first).
                buffer.append(chunk)

                # Sustained-noise cap -- tracked independently of the
                # floor-based branching just below. See the constants and
                # reasoning above _finalize_utterance() for the tiers.
                if rms > SUSTAINED_NOISE_FLOOR:
                    consecutive_below_noise_floor = 0
                    if loud_since is None:
                        loud_since = time.time()
                    else:
                        cap = (SUSTAINED_NOISE_CAP_LOUD if rms > SUSTAINED_NOISE_LOUD_RMS
                               else SUSTAINED_NOISE_CAP_MODERATE)
                        if time.time() - loud_since > cap:
                            _finalize_utterance(f"forced -- sustained noise above {SUSTAINED_NOISE_FLOOR:.0f} "
                                                 f"for over {cap:.0f}s")
                            loud_since = None
                            consecutive_below_noise_floor = 0
                            continue
                elif loud_since is not None:
                    # Don't wipe an in-progress streak on a single brief
                    # dip -- only once it's genuinely dropped for a real
                    # stretch, not just one wavering chunk.
                    consecutive_below_noise_floor += 1
                    if consecutive_below_noise_floor >= SUSTAINED_NOISE_DROP_CHUNKS:
                        loud_since = None
                        consecutive_below_noise_floor = 0

                if rms > dynamic_floor:
                    print(f"\r[hearing]  rms={rms:.0f}".ljust(40), end="", flush=True)
                    silence_start = None
                else:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > SILENCE_HANG_SECONDS:
                        _finalize_utterance("silence")
                        continue
            else:
                # Not yet confirmed as real speech -- debounced: require
                # several consecutive above-floor chunks before committing,
                # so a single brief transient (a click, a cough, a chair
                # creak -- one chunk that happens to clear the floor) can't
                # kick off a full capture and a dome "thinking" reaction on
                # its own. pre_roll keeps a short rolling window of recent
                # chunks so the ~150ms it took to debounce isn't clipped
                # off the front of a confirmed utterance.
                pre_roll.append(chunk)
                if rms > dynamic_floor:
                    consecutive_above += 1
                    if consecutive_above >= SPEECH_START_CHUNKS:
                        is_speaking = True
                        buffer = list(pre_roll)
                        silence_start = None
                        loud_since = None
                        consecutive_below_noise_floor = 0
                        print(f"\r[hearing]  rms={rms:.0f}".ljust(40), end="", flush=True)
                else:
                    consecutive_above = 0
                    # Genuinely idle chunk -- the only place the ambient
                    # estimate updates, deliberately, same rule as Pi:
                    # folding in mid-utterance pauses is what dragged the
                    # estimate down and made the floor too sensitive the
                    # first time Pi tried this.
                    alpha = AMBIENT_TRACK_DOWN_ALPHA if rms < _ambient_rms_est else AMBIENT_TRACK_UP_ALPHA
                    _ambient_rms_est += (rms - _ambient_rms_est) * alpha

                    now = time.time()
                    if now - last_status_print > 0.5:
                        print(f"\r[idle]  rms={rms:.0f}  (floor: {dynamic_floor:.0f})".ljust(50), end="", flush=True)
                        last_status_print = now

            now = time.time()
            if now - last_status_push > STATUS_PUSH_INTERVAL:
                status_queue.put({"rms": rms, "speaking": is_speaking})
                last_status_push = now
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


def run_whisper_process(emotion_queue, status_queue, keyword_queue):
    print("[WHISPER PROCESS] Starting up...")
    _import_audio_libs()
    model = load_whisper_model()
    try:
        _whisper_main(emotion_queue, status_queue, keyword_queue, model)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Entry point -- spawns both processes, connects them via three queues: one
# carries classified emotions Whisper -> BLE (as before), one carries live
# mic/status updates Whisper -> BLE for the status server to report, and
# one carries deterministic keyword-command matches Whisper -> BLE (voice
# triggers like "stay awake" or "activate hotel mode") -- kept separate from
# emotion_queue so a keyword dispatch (mode toggle, LED ping, etc.) can
# never be mistaken for a classified emotion on the receiving end, and
# separate from status_queue so a burst of status pushes can't sit in front
# of either.
# ---------------------------------------------------------------------------

def main():
    emotion_queue = multiprocessing.Queue()
    status_queue = multiprocessing.Queue()
    keyword_queue = multiprocessing.Queue()

    ble_proc = multiprocessing.Process(target=run_ble_process, args=(emotion_queue, status_queue, keyword_queue))
    whisper_proc = multiprocessing.Process(target=run_whisper_process, args=(emotion_queue, status_queue, keyword_queue))

    ble_proc.start()
    whisper_proc.start()

    try:
        ble_proc.join()
        whisper_proc.join()
    except KeyboardInterrupt:
        print("\nShutting down both processes...")
        ble_proc.terminate()
        whisper_proc.terminate()
        ble_proc.join()
        whisper_proc.join()
        print("Stopped.")


if __name__ == "__main__":
    # No-op from source; when frozen, freeze_support() is handled in main.py
    # before dispatch. Kept so `python kyber_core.py` still works directly.
    multiprocessing.freeze_support()
    main()
