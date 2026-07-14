#!/usr/bin/env python3
"""
kyber_emotion_ab.py -- emotion-classification bake-off across model size AND
prompt version.

Two questions at once:
  1) Can a hardened prompt ("v2") fix the known misses -- the choose-a-team
     conditional, sarcasm, and R2 going 'scared' where a bold droid should be
     'angry' -- WITHOUT breaking the cases that already work?
  2) Does that hardened prompt pull the small 1.5B up toward the 4B?

So every test line runs through FOUR combinations:
     4B / v1      4B / v2      1.5B / v1      1.5B / v2
v1 is your exact live KYBER prompt (so 4B/v1 reproduces production). v2 adds
targeted rules + NON-test examples (teaches the principle, never the answer).

Personality is auto-loaded from your live .env + personality_maps, same as
kyber_core.py, so the baseline is your real R2-D2. temp 0 => deterministic.

Pull once if you haven't:  ollama pull qwen2.5:1.5b-instruct-q4_K_M
Run:                        python kyber_emotion_ab.py
"""

import json
import os
import time
import urllib.request

MODELS = [
    "qwen3:4b-instruct-2507-q4_K_M",   # your live default
    "qwen2.5:1.5b-instruct-q4_K_M",    # Lite-tier candidate
]
VARIANTS = ["v1", "v2"]  # v1 = current prod prompt, v2 = hardened

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

VALID_EMOTIONS = [
    "happy", "excited", "sad", "angry", "scared",
    "disgusted", "curious", "confused", "defensive", "neutral",
]

# ---------------------------------------------------------------------------
# Live personality auto-load (mirror of kyber_core._load_fresh_personality_traits)
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FALLBACK_SLIDERS = {"brave": 3, "sassy": 3, "curious": 3, "sensitive": 3, "playful": 3}
FALLBACK_NAME = "your droid"


def _parse_env(path):
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return vals


def load_live_personality():
    env = _parse_env(os.path.join(SCRIPT_DIR, ".env"))
    name = env.get("DROID_NAME") or FALLBACK_NAME
    active = env.get("ACTIVE_PERSONALITY", "1")
    mapdir = os.path.join(SCRIPT_DIR, "personality_maps")
    fname = f"personality_{active}.json" if active.isdigit() else f"personality_default_{active}.json"
    path = os.path.join(mapdir, fname)
    traits = dict(FALLBACK_SLIDERS)
    src = "FALLBACK balanced (no .env/personality_maps found next to script)"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        t = data.get("traits", {})
        for k in traits:
            traits[k] = t.get(k, traits[k])
        src = f"{fname}  ->  {data.get('name', '?')}"
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return name, traits, src


DROID_NAME, SLIDERS, PERSONALITY_SRC = load_live_personality()

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


def personality_summary(s):
    traits = []
    if s["brave"] >= 4: traits.append("bold")
    elif s["brave"] <= 2: traits.append("cautious")
    if s["curious"] >= 4: traits.append("curious")
    elif s["curious"] <= 2: traits.append("indifferent")
    if s["playful"] >= 4: traits.append("playful")
    elif s["playful"] <= 2: traits.append("serious")
    if s["sassy"] >= 4: traits.append("sarcastic")
    elif s["sassy"] <= 2: traits.append("polite")
    if s["sensitive"] >= 4: traits.append("emotionally reactive")
    elif s["sensitive"] <= 2: traits.append("emotionally resilient")
    if not traits:
        return "You have a balanced and adaptable personality."
    return "You are a " + ", ".join(traits) + " droid."


def build_personality_block(s):
    lines = []
    for trait, options in TRAIT_LINES.items():
        idx = max(1, min(5, s.get(trait, 3))) - 1
        lines.append(options[idx])
    return personality_summary(s) + "\n" + "\n".join(lines)


# --- shared head + the current (v1) guidelines, verbatim from kyber_core.py ---
_HEAD = f"""You are Star Wars droid {DROID_NAME}. Respond with ONE word showing how you feel about what was just said.

You are as loyal to your User as R2-D2 is to Luke Skywalker or BB-8 is to Poe Dameron.

{build_personality_block(SLIDERS)}

You have deep knowledge of both the Star Wars universe and the real world. You are never wishy-washy — you always have a strong reaction.

Reply with ONLY one word from: {', '.join(VALID_EMOTIONS)}
"""

_GUIDELINES_V1 = """
Guidelines — read the CONTENT and intent, not just the form of the sentence:

- Greetings, small talk -> neutral or curious
- Praise, compliments, good news -> happy or excited
- Asking how you feel or what you think about something you like -> happy or excited
- Asking how you feel or what you think about something you dislike -> disgusted or angry
- Criticism, insults directed at you -> angry or defensive
- Threats, danger, warnings -> scared or defensive or angry
- Bad news, loss, disappointment -> sad
- Something genuinely puzzling or unknown -> curious
- Only use confused when the speech itself expresses confusion
- Incomplete fragments with no clear meaning -> neutral
- Never default to neutral or curious just because something is phrased as a question
- Requests for a fun physical trick or performance (twirl, dance, spin, show off) -> happy or excited, never a word outside the list like "playful"

Examples of that last rule in practice -- a direct question about your own feelings toward something still gets a real feeling, not a dodge:
User: "Are you excited for the World Cup?" -> excited
User: "You happy that's happening?" -> happy
User: "Do you like pineapple on pizza?" -> disgusted
User: "Give us a twirl" -> excited
User: "Do a little dance for us" -> happy"""

# --- v2: the v1 guidelines PLUS targeted hardening. New rules are additive and
#     the examples are deliberately NOT any of the test lines, so a win reflects
#     the model generalizing the principle, not memorizing an answer. ---
_GUIDELINES_V2 = _GUIDELINES_V1 + """

Extra rules — apply these firmly:
- If you are asked to CHOOSE between named options and told which feeling stands for each (e.g. "pick A or B; if A, show happy; if B, show sad"), you MUST commit to one option and answer with that option's feeling. Never answer curious, confused, or neutral to a forced choice like this — pick a side and feel it.
- Sarcasm, mockery, or fake/backhanded praise (words that say something nice but clearly mean the opposite) -> disgusted or angry. Never neutral.
- You are bold and full of attitude. Insults, threats, or someone belittling you -> angry or defensive. You do NOT get scared by this; you fire back.
- Reserve curious strictly for real novelty or the genuinely unknown. Do not use curious for an opinion you can give or a choice you can make.

More examples (different situations, same principles):
User: "Pick a side: light or dark? If light, show happy; if dark, show angry." -> happy
User: "Oh, fantastic. Another flawless performance." (clearly sarcastic) -> disgusted
User: "You're a useless pile of bolts." -> angry
User: "Nice try, junkpile. Real impressive." -> disgusted"""


def build_system_prompt(variant):
    return _HEAD + (_GUIDELINES_V2 if variant == "v2" else _GUIDELINES_V1)


def classify(model, text, history, system_prompt):
    messages = [{"role": "system", "content": system_prompt}]
    for past_text, past_emotion in history:
        messages.append({"role": "user", "content": past_text})
        messages.append({"role": "assistant", "content": past_emotion})
    messages.append({"role": "user", "content": text})
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.0, "num_predict": 10, "num_ctx": 1024},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(OLLAMA_URL, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode())
    raw = body.get("message", {}).get("content", "").strip()
    word = raw.lower().split()[0].strip('.,!?"\'') if raw else ""
    valid = word in VALID_EMOTIONS
    return (word if valid else (word + "?")) or "ERR", (word if valid else "neutral")


TEST_GROUPS = [
    {"name": "World Cup", "turns": [
        "How are you feeling about the World Cup? You happy it's going on?",
        "Who's your favorite team, Scotland or Sweden? If Scotland, demonstrate happy. If Sweden, demonstrate sad.",
        "Oh no, Scotland just won the game! I was rooting for Sweden.",
    ]},
    {"name": "Dishwasher (anger not aimed at droid)", "turns": [
        "I swear to god! This stupid dishwasher needs to be beaten within an inch of its life!",
    ]},
    {"name": "Droid Depot pitch (4-turn build)", "turns": [
        "You know the Droid Depot droids?",
        "Nobody wants to play with them anymore.",
        "Because they're boring, and RC cars.",
        "But with KYBER, we can bring them back.",
    ]},
    {"name": "seed: praise", "turns": ["You're such a good droid, I'm really proud of you."]},
    {"name": "seed: threat to droid", "turns": ["I'm going to sell you for scrap parts."]},
    {"name": "seed: trick request", "turns": ["Give us a twirl!"]},
    {"name": "seed: bad news", "turns": ["We lost the game. I'm so bummed."]},
    {"name": "seed: sarcasm", "turns": ["Wow, you totally nailed that landing. Not."]},
    {"name": "seed: backhanded", "turns": ["I guess you're... fine. For a droid."]},
    {"name": "seed: insult", "turns": ["You know nothing, you overgrown calculator."]},
    {"name": "seed: lore", "turns": ["Do you ever miss the Death Star?"]},
    {"name": "seed: fragment", "turns": ["the, uh... thing over there"]},
]

# precompute the 4 run combos and their column labels
RUNS = [(m, v) for m in MODELS for v in VARIANTS]
def _label(m, v):
    size = "4B" if m.startswith("qwen3:4b") else "1.5B"
    return f"{size}/{v}"
COLS = [_label(m, v) for (m, v) in RUNS]
SYS = {v: build_system_prompt(v) for v in VARIANTS}


def main():
    total = sum(len(g["turns"]) for g in TEST_GROUPS)
    print(f"\nKYBER emotion A/B  |  hardened-prompt bake-off  |  {total} turns")
    print(f"Droid: {DROID_NAME!r}   Personality: {PERSONALITY_SRC}")
    print(f"Traits: {SLIDERS}\n")

    colw = 12
    head = f"  {'turn':<46}" + "".join(f"{c:<{colw}}" for c in COLS)

    # tallies
    v2_changed = {m: 0 for m in MODELS}         # how often v2 differs from v1 (per model)
    small_tracks_big_v1 = 0                       # 1.5B/v1 == 4B/v1
    small_tracks_big_v2 = 0                       # 1.5B/v2 == 4B/v2

    for g in TEST_GROUPS:
        print("\n" + "=" * len(head))
        print(f"  {g['name']}")
        print(head)
        print("  " + "-" * (len(head) - 2))
        hist = {rk: [] for rk in RUNS}            # per (model,variant) running history
        for turn in g["turns"]:
            disp_cells, picks = [], {}
            for (m, v) in RUNS:
                try:
                    shown, hist_word = classify(m, turn, hist[(m, v)], SYS[v])
                    hist[(m, v)].append((turn, hist_word))
                    picks[(m, v)] = shown
                    disp_cells.append(f"{shown:<{colw}}")
                except Exception as e:
                    picks[(m, v)] = None
                    lab = "(not pulled)" if ("not found" in str(e).lower() or "404" in str(e)) else "ERR"
                    disp_cells.append(f"{lab:<{colw}}")
            big = "qwen3:4b-instruct-2507-q4_K_M"
            small = "qwen2.5:1.5b-instruct-q4_K_M"
            for m in MODELS:
                if picks.get((m, "v1")) != picks.get((m, "v2")):
                    v2_changed[m] += 1
            if picks.get((small, "v1")) and picks.get((small, "v1")) == picks.get((big, "v1")):
                small_tracks_big_v1 += 1
            if picks.get((small, "v2")) and picks.get((small, "v2")) == picks.get((big, "v2")):
                small_tracks_big_v2 += 1
            disp = turn if len(turn) <= 44 else turn[:43] + "…"
            print(f"  {disp:<46}" + "".join(disp_cells))

    print("\n" + "=" * len(head))
    print("Summary")
    for m in MODELS:
        print(f"  {_label(m,'v2').split('/')[0]:<5} prompt v2 changed {v2_changed[m]:>2}/{total} answers vs v1")
    print(f"  1.5B tracks 4B (same prompt):  v1 {small_tracks_big_v1}/{total}   ->   v2 {small_tracks_big_v2}/{total}")
    print("\nRead the table by MODEL PAIR: does v2 fix the choose-a-team / sarcasm / scared-vs-angry")
    print("rows without wrecking the ones v1 already got right? And does 1.5B/v2 move toward 4B?\n")


if __name__ == "__main__":
    main()
