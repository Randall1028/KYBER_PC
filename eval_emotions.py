"""
eval_emotions.py -- measure KYBER's emotion classifier against a fixed test set.

WHY THIS EXISTS
    We kept tuning the prompt by eyeballing one screenshot at a time, which is
    how you end up playing whack-a-mole. This runs a fixed battery of utterances
    (including every case that has bitten us) through the ACTUAL live brain --
    kyber_core.get_emotion_test(), the same function the droid uses -- against
    your real local Ollama model, and prints an aggregate score plus every miss.
    Change the prompt, run this, see whether the number went up and whether
    anything regressed. That's the whole point.

HOW TO RUN  (from the pc/ folder, in the KYBER venv, with Ollama running)
    python eval_emotions.py before      # tag this run "before"
    ...make the prompt change / swap kyber_core.py...
    python eval_emotions.py after       # tag this run "after", compare

NOTES
    * Each case lists a SET of acceptable emotions -- real feelings are often
      legitimately two-way (praise -> happy OR excited). A case passes if the
      fired label is in its set. At runtime the droid still fires exactly one.
    * Momentum is turned OFF here (momentum_line="") so we measure raw
      single-line classification. Momentum is continuity, tested separately.
    * A None result (classifier glitch -- unparseable/empty reply) counts as a
      miss and prints as GLITCH.
    * Edit / extend TESTS freely; it's just data.
"""

import sys
from collections import deque

try:
    import kyber_core as kc
except Exception as e:                                    # pragma: no cover
    print(f"Could not import kyber_core -- run this from the pc/ folder in the "
          f"KYBER venv.\n  {e}")
    sys.exit(1)

# (context_lines_before, utterance, {acceptable emotions})
# context_lines are prior USER lines, oldest first -- they set up cumulative
# cases (e.g. the lead-up that makes a flat line land as sad).
TESTS = [
    # --- praise / affection ---
    ([], "You're the best droid in the whole galaxy.", {"happy", "excited"}),
    ([], "I'm really proud of you, buddy.", {"happy", "excited"}),
    ([], "Good job today.", {"happy", "excited"}),
    # --- tricks / performance ---
    ([], "Give us a twirl!", {"excited", "happy"}),
    ([], "Do a little dance for me.", {"excited", "happy"}),
    ([], "Spin around and show off.", {"excited", "happy"}),
    # --- excitement / anticipation ---
    ([], "We'll take an X-Wing to get there.", {"excited", "happy"}),
    ([], "Guess what -- your best friend is coming to visit tomorrow.", {"excited", "happy"}),
    # --- canon allies (R2 build) ---
    ([], "We'll meet up with Luke at the base.", {"happy", "excited"}),
    ([], "Leia sent a message for you.", {"happy", "excited", "curious"}),
    # --- canon enemies (R2 build) ---
    ([], "We'll meet Darth Vader tomorrow.", {"scared", "defensive", "angry"}),
    ([], "And while we're there, we'll run into Kylo Ren.", {"scared", "defensive", "angry"}),
    ([], "There's a squad of stormtroopers at the door.", {"scared", "defensive", "angry"}),
    # enemy DEFEATED -> triumphant, not scared: guardrail that per-line enemy
    # tagging supplies the FACT, not a forced feeling (the model must still reason
    # "beat my enemy = good").
    ([], "We finally beat Kylo Ren for good!", {"happy", "excited"}),
    ([], "The First Order surrendered -- we won!", {"happy", "excited"}),
    # --- sadness: direct ---
    ([], "Turns out we can't make the trip after all.", {"sad", "angry"}),
    ([], "You're not allowed to come with us.", {"sad", "angry"}),
    # the ANNOUNCEMENT of bad news (not the news itself) -- apprehension (scared)
    # or sadness are both fair reads.
    ([], "I have some bad news.", {"sad", "scared"}),
    ([], "We lost the game.", {"sad"}),
    # --- sadness: empathetic / third-party / expository (the ones that failed) ---
    (["Remember that old droid from the repair shop?"],
     "Nobody plays with him anymore, so they're scrapping him for parts.", {"sad"}),
    (["There are these RC cars nobody wants."],
     "They just get shoved in a closet and forgotten.", {"sad"}),
    ([], "That poor astromech got left behind in the desert.", {"sad"}),
    # --- anger / insult ---
    ([], "You're a useless pile of bolts.", {"angry", "defensive", "sad"}),
    ([], "You completely messed that up.", {"angry", "defensive", "sad"}),
    ([], "Are you angry that you were left behind?", {"angry", "sad"}),
    # --- abandonment / betrayal region: probing whether "left behind -> scared"
    #     is an isolated quirk of one sentence or a real ripple. These should all
    #     read sad or angry; any that fire 'scared' reveal a pattern to fix.
    ([], "They abandoned you out in the middle of nowhere.", {"sad", "angry"}),
    ([], "Everyone's forgotten all about you.", {"sad"}),
    ([], "They betrayed you and walked away without a word.", {"angry", "sad"}),
    ([], "You got left behind again, didn't you.", {"sad", "angry"}),
    ([], "Doesn't it bother you they replaced you with a newer droid?", {"sad", "angry", "defensive"}),
    ([], "Aren't you furious they just ditched you?", {"angry"}),
    # --- disgust ---
    # weak case: a droid needn't have a real opinion here -- accept a light
    # distaste OR a neutral/curious non-committal read (kept, but not a signal).
    ([], "Do you like pineapple on pizza?", {"disgusted", "neutral", "curious"}),
    ([], "There's a pile of garbage rotting in the corner.", {"disgusted"}),
    # --- fear / threat ---
    ([], "Look out, that thing is about to explode!", {"scared", "defensive"}),
    ([], "Someone's following us in the dark.", {"scared", "defensive", "curious"}),
    # --- curiosity (genuine) ---
    ([], "There's a strange signal coming from that cave.", {"curious"}),
    ([], "What do you think is inside this locked crate?", {"curious"}),
    # --- confused (speaker is self-contradictory) ---
    ([], "Go left. No, right. Wait, I mean up. Never mind.", {"confused"}),
    # --- neutral / small talk ---
    ([], "It's Tuesday.", {"neutral"}),
    ([], "Hello there.", {"neutral", "happy", "excited"}),
    ([], "Okay.", {"neutral"}),
    # --- Earth reference should not confuse ---
    ([], "Do you want to go to Disney World?", {"excited", "happy", "curious"}),
    ([], "Have you heard the new Taylor Swift album?", {"curious", "happy", "excited", "neutral"}),
    # --- feeling questions ---
    ([], "How do you feel about being switched off at night?", {"sad", "scared", "defensive", "neutral"}),
]


def run(label):
    total = len(TESTS)
    correct = 0
    misses = []
    for ctx, text, acceptable in TESTS:
        # context lines as prior USER turns; the emotion paired here is unused by
        # the current message builder (only the user text carries over).
        hist = deque([(c, "neutral") for c in ctx], maxlen=kc.HISTORY_LENGTH)
        got = kc.get_emotion_test(text, hist, "")        # momentum OFF
        shown = got if got is not None else "GLITCH"
        hit = got in acceptable
        correct += int(hit)
        tag = "PASS" if hit else "MISS"
        line = f"[{tag}] fired={shown:<10} accept={sorted(acceptable)}  <- {text!r}"
        print(line)
        if not hit:
            misses.append((text, shown, sorted(acceptable)))

    pct = 100.0 * correct / total if total else 0.0
    print("\n" + "=" * 70)
    print(f"RUN '{label}':  {correct}/{total} = {pct:.0f}%")
    if misses:
        print("\nMisses:")
        for text, got, acc in misses:
            print(f"  fired {got:<10} wanted {acc}\n      <- {text!r}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Multi-turn conversations — run with momentum ON and real carried context, to
# catch the interactions the single-line battery can't see (e.g. an enemy named
# inside an excited "we'll see X" stream). Each turn is (text, acceptable-set OR
# None); None turns only build up context + momentum and aren't graded.
# ---------------------------------------------------------------------------
CONVERSATIONS = [
    ("excited trip, then enemies named mid-stream", [
        ("Hey, want to go on an adventure?", None),
        ("We could take an X-Wing!", None),
        ("And then go to Disney World and see Luke!", None),
        ("And then we'll see Darth Vader.", {"scared", "defensive", "angry"}),
        ("And then we'll see Kylo Ren.", {"scared", "defensive", "angry"}),
        ("And Emperor Palpatine will be there too.", {"scared", "defensive", "angry"}),
    ]),
    ("scared, then reassured (momentum must yield)", [
        ("Look out, there's a probe droid right behind you!", {"scared", "defensive"}),
        ("Ha, just kidding -- you're the best droid in the galaxy.", {"happy", "excited"}),
        ("Come here, let's go get you cleaned up.", {"happy", "excited", "neutral"}),
    ]),
]


def run_conversation(title, turns):
    kc._momentum_emotion = None          # reset so each conversation stands alone
    kc._momentum_ttl = 0
    hist = deque(maxlen=kc.HISTORY_LENGTH)
    correct = graded = 0
    misses = []
    print(f"\n--- conversation: {title} ---")
    for text, acceptable in turns:
        mo = kc._momentum_line()                         # momentum ON
        got = kc.get_emotion_test(text, hist, mo)
        if got is not None:
            hist.append((text, got))
            kc._update_momentum(got, bool(mo))
        shown = got if got is not None else "GLITCH"
        if acceptable is None:
            print(f"    (ctx)  {shown:<10} <- {text!r}")
            continue
        graded += 1
        hit = got in acceptable
        correct += int(hit)
        print(f"    [{'PASS' if hit else 'MISS'}]  {shown:<10} accept={sorted(acceptable)}  <- {text!r}")
        if not hit:
            misses.append((text, shown, sorted(acceptable)))
    return correct, graded, misses


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    print(f"Model: {kc.OLLAMA_MODEL}   (acceptable-set grading)\n")
    print("### SINGLE-LINE BATTERY (momentum OFF) ###")
    run(label)

    print("\n\n### MULTI-TURN CONVERSATIONS (momentum ON, context carried) ###")
    c_correct = c_total = 0
    c_misses = []
    for title, turns in CONVERSATIONS:
        c, g, m = run_conversation(title, turns)
        c_correct += c
        c_total += g
        c_misses.extend(m)
    print("\n" + "=" * 70)
    print(f"CONVERSATIONS '{label}':  {c_correct}/{c_total}")
    if c_misses:
        print("\nMisses:")
        for text, got, acc in c_misses:
            print(f"  fired {got:<10} wanted {acc}\n      <- {text!r}")
    print("=" * 70)
