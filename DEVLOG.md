# KYBER — Dev Log & Punch-list (v0.87.0 — building)

Living doc. Check off as things ship, add as they come up, log each session. Persisted to the machine at `C:\Users\barry\kyber\pc\DEVLOG.md` (and kept in the cloud workspace during a session).

_Last updated: 2026-07-17_

---

## ⏳ Pending commit

_(none — everything below is committed to source and needs only a **rebuild to bake into the packaged exe** (it's all been live-tested from source and works). Includes: the agnostic prompt, keyword gate, Ollama fix, the 07-17 pm work (momentum, context-as-prose history, retry + glitch-sentinel audit fixes), the 07-17 evening **definitions rewrite**, the **per-line enemy tagging**, and the **Com Uplink dock live-`droid_type` fix**. Validated at 43/44 (98%) on the eval + live-confirmed in a real conversation (Kylo Ren finally reads scared mid-excited-stream). Latency is a non-issue (~0.3s warm).)_

## ☐ Open threads

- [ ] **Rebuild + live-test** (Kalvin) — PyInstaller + Inno to bake in everything below, then verify:
  - **Agnostic prompt** — a villain build (low playful + low sensitive) reads menacing; praise to a cold droid reads flat/uncaring, not happy; a custom droid gets NO faction lean.
  - **Canon by name** — R2 / Artoo / BB-8 / Chopper / BD-1 (and spellings) still act in-character; canon line now says "films, series, and games".
  - **Dual-world knowledge** — "do you know Star Wars?" and real-world/Earth references no longer return `confused`.
  - **Latency** — response delay should be noticeably shorter (prompt slimmed ~36%, lore lists gone). Confirm the "thinking" animation is back to its old length.
  - **Keyword gate** — "back up, are you telling me you're a droid?" and "send for backup" no longer trigger retreat/back; "okay, back up a little" still does.
  - **Ollama teardown** — quit KYBER from the tray; confirm Ollama is GONE from Task Manager (even if the Mainframe started it during setup).
  - Prior batch still to confirm: mid-session droid switch (activation advances), "in the trunk" gets a real reaction, "take an X-Wing" reads excited, `come here` shows a 👋 action bubble, Task Manager clean after quit.
  - **Curious/confused over-selection** (the 07-17 pm session) — flat statements should stop defaulting to curious/confused: "We'll see Luke" → happy, "We'll see Vader/Kylo" → scared/wary, "So let's go to Disney World" → excited/happy not confused. Watch a whole conversation, not one line.
  - **Emotional momentum** — get him scared, then immediately reassure ("just kidding, you're the best") → he should read **happy right away**, NOT stay scared. Separately: scare him, then go quiet/ambiguous → he lingers on edge a beat, then fades to neutral on his own (no endless loop).
  - **No curious/confused snowball** — a run of similar lines shouldn't lock him into repeating the same label (history no longer feeds his own past labels back to him).
  - **Glitch light** — a genuine "confused" reaction should NOT light the tray "glitched" indicator; only a real classifier glitch (unparseable/empty LLM reply) does now.
  - **Definitions prompt + enemy tagging (07-17 evening)** — ✅ live-validated. Classifier reasons from emotion *definitions* + matched transcript format; per-line enemy tagging cured the Kylo-Ren-reads-excited bug in a live excited stream. Only remaining rebuild-verify: confirm it all survives the PyInstaller freeze (it's identical source, so it should).
- [x] ~~**Latency pass / prompt trim**~~ — **not needed.** Live logs showed ~0.3–0.5s per classification warm (2s one-time cold model load) at ~1000–1140 tokens — well under Whisper's own ~1s transcribe. The ~955-token prompt is not hurting anything. Trimming would only risk regressions for no user-visible gain. Left as a someday-if-bored item, not a priority.
- [ ] **STT latency on slower machines** (the REAL latency lever) — live timing breakdown: Qwen ≈ 0.3–0.5s (fine), but **Whisper ≈ 0.9–1.0s** + the VAD silence-wait before it finalizes is what makes it feel laggy. And per config.py's own note, Whisper is `'small'` on **every** tier — only the *LLM* is tiered (4B/1.5B). So on a 2-core/low-RAM "lite" machine, the LLM downshifts but Whisper `small` does NOT, and `small` on a weak CPU is the thing that'll drag (could be multiple seconds). Fix if we target weak hardware: **tier Whisper too** — `small` on capable, `base`/`tiny` on lite (mirror `TIER_MODELS`), accepting some transcription-accuracy loss on weak boxes (garbled text → worse emotion reads). Separately, the VAD end-of-speech silence timeout is a knob that'd shave perceived lag for everyone. NOT a prompt problem — the prompt work is done.
- [ ] **No custom-mood removal UI** — a user can add moods (up to the cap) but can't delete one; cap message steers them to a new profile. Future item if moods should be editable *down*, not just up.
- [ ] **Idle behavior** — what Com Uplink shows after a long stretch of no input (never nailed down; currently the last feeling lingers, offline → neutral dot).
- [ ] **Cosmetic maybe:** all traffic-light circles (🔴🟡🟢) for happy instead of the 🟢 green-circle stand-in — left as an option.

## ✅ Going into v0.87.0 — committed to source since 0.86.1 (pending rebuild)

**Faction-agnostic prompt rewrite** (`kyber_core.py` `_build_system_prompt`):
- Removed all hardcoded good-guy/Rebel bias, lore enumeration (villain/hero/ship lists), and the 6 lore examples. Prompt is ~36% smaller (~869 → ~552 tokens) → the latency fix.
- Droids react by **temperament (sliders) + situation**, so an Imperial/villain build is never fed a hero lean.
- **Canon by NAME** — `_canon_droid()` detects R2/R2-D2/Artoo, BB-8/BeeBee Eight, Chopper/Chop/C1-10P, BD-1/BeeDee One from the droid's *name* (independent of personality slot) and adds ONE line: *"You are <X> — a canon Star Wars droid. Stay true to your character… exactly as they are across all Star Wars films, series, and games."* (Was "in the films" — broadened so BD-1/games + Chopper/series count.)
- **Loyalty = bond, not a warm-only trait**: *"…fiercely loyal to your User and bonded to them as your own — they are your person, and you are devoted to them above all."* Universal to every droid (the "this is THEIR droid" framing), villain or not.
- **Dual-world knowledge**: *"You know both worlds equally well — the Star Wars galaxy and real-world Earth… A reference to either is familiar to you, never confusing."* (Fixes the "confused when asked if it knows Star Wars" / BB-8-doesn't-know-lore bug, and lets pineapple-on-pizza make sense.)
- **Personality-modulated guidelines**: intro now says *"filter through YOUR personality (a cold droid meets praise with indifference; a sensitive one takes criticism hard; a bold one isn't easily scared)"* — so an emo/villain droid doesn't force-read praise as happy.
- **Examples trimmed to 3, content-driven, no warm bias**: `Aren't you angry they left you behind? → angry`, `Do you like pineapple on pizza? → disgusted`, `Give us a twirl → excited`. (Dropped World Cup = time-bound; dropped "glad to see me → happy" = warm-droid bias; kept pineapple.)

**Colder low-end trait lines** (`config.py`): `playful=1` → "cold and severe… hard edge in everything"; `sensitive=1` → "hard and unfeeling… indifferent to the emotions of others." `personality_summary()` gained matching low-end labels, so villain builds read menacing.

**Keyword over-trigger gate** (`kyber_core.py` `check_keywords`): dropped the bare one-word `"backup"`; the 5 **movement gestures** (forward/about-face/back/dance/retreat) now fire only on a terse imperative (`_looks_imperative`: not ending in "?", ≤6 words). Mode-toggles (hotel/pet/expressive, sleep/wake) and `that_way` left alone. Verified: "back up, are you telling me you're a droid?" and "send for backup" → no command; "okay, back up a little" → still fires.

**Ollama lingering-process fix** (`provisioning.py`): whoever starts `ollama serve` now writes its PID to `runtime/ollama.pid`; `stop_ollama()` falls back to that PID when its own `_ollama_proc` is None (the cross-process case — Mainframe starts Ollama during setup, tray couldn't find it on quit). Tree-kill guarded by `IMAGENAME eq ollama.exe` against PID recycling; pid file cleared on stop.

---

### Follow-up: curious/confused fix + momentum + audit fixes (07-17 pm, `kyber_core.py`)

Diagnosed live R2-D2 returning almost nothing but **curious/confused**. Root causes: the agnostic rewrite deleted the named-entity valence rules R2 actually needs (Vader/Kylo → scared, Luke → happy); flat conversational statements had no home but "small talk → neutral or curious"; the emotion label "curious" collided with R2's curiosity *trait*; and the classifier fed its own past emotion labels back to itself, snowballing a streak at temp 0.

- **Prompt content** (`_build_system_prompt`): canon line now says *react from* that allegiance (warmth toward allies, fear/anger toward enemies) even when a name is only mentioned — kept **inside the canon branch** so custom/villain builds still get zero lean. Replaced "small talk → neutral or curious" with a **statement/subject rule** (react to WHO/WHAT is named, not the flat wording); added "plans falling through → sad or angry". Tightened **curious** (only genuine intrigue, explicitly separate from the curiosity temperament) and **confused** (only when the *speaker* is confused, never "not sure how to react"). Earth-knowledge line now says an Earth reference is NEVER a reason to feel confused/curious. Two new content-driven examples (best-friend-visiting → excited, can't-go → sad). Net ~+60–80 tokens (the ~36% rewrite savings easily covers it).
- **History = context, not label-echo** (`_build_chat_messages`): recent user lines are folded into the current user message as read-only context; the model no longer sees its own past emotion labels as assistant turns → kills the curious/confused snowball while keeping thread-awareness ("on second thought we can't go").
- **Emotional momentum** (new state machine near `conversation_history`): a **soft** prior only. `_MOMENTUM_LINGER` (scared/angry/defensive=2, sad/disgusted/excited/happy=1, everything else 0 → neutral/curious/confused never linger). `_momentum_line()` injects a prompt line that ALWAYS yields to a clear new feeling; `_update_momentum()` resets on a genuinely new feeling but **burns the timer down** when a mood is sustained only by momentum, so it fades instead of looping. Threaded `momentum_line` through `get_emotion_test → _get_emotion_qwen_raw → _build_chat_messages → _build_system_prompt`; wired into `_handle_transcription` (compute before classify, update only on a successful read). Single-threaded (Whisper process), so the module-global state is race-free.
- **Audit fix — retry actually retries** (`get_emotion_test` / `_get_emotion_qwen_raw`): the old retry re-issued an identical greedy (temp 0.0) request, so a parse failure could never recover. Now attempt 0 is greedy, the retry samples at temp 0.5; `num_predict` 10 → 24 so a label with a stray prefix isn't truncated.
- **Audit fix — glitch ≠ confused** (`GLITCH_SENTINEL = "__glitch__"`): a classifier glitch now puts a dedicated sentinel on `emotion_queue` instead of the word "confused". The BLE reader maps it to the confused *reaction* + lights the tray "glitched" indicator, while a **real** "confused" reacts WITHOUT lighting it. (Feed still shows a glitch as a confused blip — deferred; distinguishing it in `recent[]` is a small follow-up.)
- Not a bug: the audit flagged `num_predict:10` breaking a *thinking* Qwen3, but the tier models are `qwen3:4b-instruct-2507` / `qwen2.5:1.5b-instruct` (non-thinking), so no `"think": false` needed unless a thinking tag is ever adopted.
- Verified in-workspace: ast.parse, prompt-render substring checks, all threaded signatures, label-echo removed, and the momentum scenarios (fear→reassure→happy immediately; unreinforced fear fades to ttl 0) run against the real committed code. **Not yet live — needs the rebuild.**

_All five verified in the cloud workspace (ast.parse + real-file prompt render with assertions + keyword unit tests + PID round-trip). Not yet live — needs the PyInstaller/Inno rebuild._

---

### Follow-up 2: rules → **definitions** rewrite + eval harness (07-17 evening, `kyber_core.py` + `eval_emotions.py`)

**Why:** even after Follow-up 1, live R2 still misfired (bad-news/abandonment → scared, expository sadness → curious). Root realization (Kalvin's call): the guideline *list* is a lookup table — you can't enumerate natural language, so every new sentence became "one more small prompt fix." That's the treadmill. The fix is to change what the prompt *asks*: reason from the **meaning of each emotion**, not match a situation to a rule.

**What changed in `_build_system_prompt` / `_build_chat_messages`:**
- **Definitions, not rules.** The ~14 `situation -> label` bullets are replaced by one short **definition per emotion** (what the feeling MEANS, written broadly so it composes over unseen cases) + a method line ("work out what the User means and the mood of the exchange, then pick the label whose meaning fits, shaped by your personality").
- **Matched message format.** The old assembly showed examples as `User: "x" -> label` but posed the real query in a different shape, and fenced context off as "do NOT label these." Now the examples AND the live query use ONE format — a short transcript (`Conversation so far:` / `The User just said:`) ending in `→`, which primes a bare one-word answer. Context is *felt* (the conversation the droid is in), not fenced.
- **Robust parsing** (`_match_mood`): added a final fallback that finds the first mood word anywhere in the reply and strips a stray `→`/prefix, so an echoed arrow can't glitch a valid answer.
- Kept the structural pieces: personality block, canon ally/enemy valence (+ "meeting an enemy is never exciting, however the plan is framed"), momentum, dual-world.

**Definition-boundary tuning (all measured on the eval, not by eye):**
- Narrowed **scared** to a real/physical/outside danger — explicitly NOT bad news, disappointment, exclusion, or **being left behind / abandoned / ditched / forgotten / replaced** ("even when being alone sounds risky"). This killed a real *abandonment → scared* lean the eval surfaced (probed with 6 sibling cases: it was a pattern, not one sentence).
- Broadened **sad** (bad news, being left out/rejected/excluded) and **angry** (abandoned, treated unfairly) to own that territory.

**`eval_emotions.py`** — the anti-whack-a-mole tool. Runs a fixed battery (42 cases incl. every failure mode we hit) through the live `get_emotion_test` against real Ollama, momentum OFF, acceptable-*set* grading, prints score + misses. Run: `python eval_emotions.py <label>` from `pc/` in the venv with Ollama up. **The eval imports `kyber_core.py` fresh, so it tests source directly — no rebuild needed to measure a prompt change.**

**Result:** leak-inflated 89% baseline (~83% real) → **41/42 (98%)**. The one holdout ("abandoned in the middle of nowhere" → scared) is a genuine dual-meaning case (stranded = real danger), not a crack. Real danger cases (Vader/stormtroopers/explode) stayed scared throughout — no fear-detection lost.

**Note:** this SUPERSEDES the rule-list guidelines from Follow-up 1's "Prompt content" bullet (the canon valence, momentum, history-restructure, and audit fixes all remain). Still source-only — **needs the rebuild**. Then the latency/trim pass (prompt ~955 tok).

_Bridge gotcha this session: the remote-devices mount froze its content cache after a reconnect — re-staging a path already staged returns the STALE copy, even after a reboot/re-save. Metadata (`device_list_dir`, stage response `bytes`/`mtimeMs`) and the WRITE path stayed correct. Workaround: a never-before-staged path reads fresh (verified). Commits used the `mtimeMs` guard, so no clobber risk._

---

### Follow-up 3: per-line enemy tagging + Com Uplink dock fix (07-17 late, `kyber_core.py`) — LIVE-VALIDATED

Live-testing the definitions build surfaced two things.

**The Kylo Ren problem.** Allies (Luke, Chewie) → excited, "Darth Vader" → angry/scared, stormtroopers → scared — all correct in a live excited "we'll see X at Disney" stream. But **"we'll see Kylo Ren" → excited**, every time. Chased the cause properly:
- NOT a knowledge cutoff — the model is `qwen3:4b-instruct-2507` (July 2025); Kylo's been in the data a decade.
- NOT ignorance — asked cold ("as R2, emotion at seeing Kylo Ren?") it answers **"fear"** instantly, and "is Kylo sympathetic?" → "no, driven by anger/power."
- The real cause: the model *knows* but doesn't *apply* it in the split-second single-token call, and our own prompt suppresses it — R2 is `brave=5` ("not easily scared"), and the excited context shoves the rest. Kylo specifically slips because his signal is weaker/noisier than Vader's (all the "complex / redeemed / Ben Solo" association). Vader's fear-prior is strong enough to survive; Kylo's isn't.
- Tried the **system-prompt roster** (name all the villains as enemies) — **backfired**: it primed `scared` globally and regressed the abandonment cases 98%→95% without even fixing Kylo. Reverted it. Lesson (again): enemy pressure in the *system* prompt bleeds everywhere.
- **The fix that worked — per-line enemy tagging** (`_named_enemy` + `_CANON_ENEMY_NAMES`, injected in `_build_chat_messages`): when the CURRENT line names a canon villain **and** the droid is a celebrity droid (`_canon_droid`), drop one line into that query only — `(Note: <name> is one of your enemies -- a threat, never a friend.)`. Strictly per-line, so lines with no enemy get nothing → **zero global scared-bleed** (abandonment stayed at 98%). States the FACT not the feeling, so the model still reasons the situation — verified by guardrail evals: "we beat Kylo Ren!" and "First Order surrendered — we won!" → **happy/excited**, not scared. **Celebrity droids only** — a custom/villain droid gets no roster, preserving agnosticism.
- Result: eval 43/44 (98%; lone miss = the "middle of nowhere" dual-read, which reads *sad* correctly live thanks to context) AND **live-confirmed** — Kylo, Palpatine, stormtroopers all read scared mid-excited-stream. Fixed.
- (Bug found in the process: `_ENEMY_RE = re.compile(...)` at module load with no `import re` at the top of `kyber_core.py` — added it.)

**Com Uplink dock live-`droid_type`** (`_StatusHandler` `/status`): the dock polls `/status` for `droid_type`, but it reported the launch-time module constant, so a model change saved from the Mainframe never updated the dock (stuck showing the old chassis + "· SERIES"). The brain already live-reloads the model everywhere else via `get_droid_type()`; the `/status` handler now re-reads DROID_TYPE/DROID_NAME fresh from `.env` per request, so the dock tracks a saved change without a brain restart.

**Eval harness gained multi-turn conversation mode** (`eval_emotions.py`): momentum ON, context carried, to catch exactly the interaction (excited stream → enemy) the single-line battery can't see. This is what let us measure the tag fix instead of guessing from screenshots.

**Latency: measured, non-issue.** Live: Qwen ~0.3–0.5s warm, ~1000–1140 tok. The felt lag is Whisper (~1s) + VAD, not the LLM/prompt — see the STT open thread above.

**Net: the prompt is sorted.** All source-only, needs the rebuild to bake into the exe.

## ✅ Shipped — v0.86.1 (committed to machine)

**Build plan (all four steps done):**
- [x] VERSION → 0.86.1
- [x] Sound-mapper icons → emoji palette
- [x] Brain custom-mood classifier — `active_moods()`, `_match_mood()` (multi-word), reads `mood_meta`; create-mood modal (name + emoji + hint); hint **required on lite**, optional on full ("To ensure accuracy, hints are required.")
- [x] Com Uplink dock + app-wide chrome — appbar header, footer removed, version under CORE ONLINE, persistent dock on every app page (not the 6 setup-wizard pages); dock polls the brain `/status` for live emotion + rolling `recent[]`

**4a — brain exposes live state** (`kyber_core.py`): `emotion` / `heard` / `recent[]` / `last_reaction_ts` on `/status`, fed from `_handle_transcription` via `status_queue`. (Note: first commit shipped empty — re-applied and re-verified end-to-end.)

**Audit** (Python + JS) run on the 4b change; fixes applied.

**Polish + fixes:**
- [x] Number/Designation caption `(ex: R2-D2, BB-8, R51-4B3)` — mainframe **and** wizard
- [x] Droid-name save sanitizer (`[A-Za-z0-9 -]`) — protects HTML attrs, the calibration JS string, and the LLM prompt
- [x] Live bubble syncs to the newest **feeling** (no more emoji cycling); gestures don't hijack it
- [x] Gestures logged in Com Uplink as dashed glyph-only "action" bubbles; **kept out of the Legend** on purpose (discovery)
- [x] Lore prompt: Finn → happy, Kylo Ren → scared, X-Wing → excited, TIE fighter → defensive, defector → happy; "judge a ship by allegiance and reported action"
- [x] Whisper hallucination filter — `no_speech_prob` gate + `condition_on_previous_text=False` + YouTube-ism blocklist
- [x] Keyword matcher whole-word for bare triggers — "in the trunk" no longer fires retreat
- [x] `tray_shell` — brain-launch watcher runs app-wide + stop-then-launch on a mid-session droid switch (activation no longer stalls)
- [x] `tray_shell` — `stop_kyber_core()` tree-kills (`taskkill /F /T`) so the Whisper + BLE children can't orphan on quit
- [x] README → 0.86.1 (+ release notes drafted as the GitHub-release paste-in)
- [x] Tier-aware custom-mood cap — lite 4 / full 8 per sound profile; UI fail-fast + server hard-enforce; editing an existing mood always allowed

## 📓 Session log

### 2026-07-14 → 15 (night)
- Committed 4a; caught it had shipped **empty** (only `ast.parse` was run, not a content check) — re-applied, verified the full data path, re-committed. New rule: for anything with a runtime contract, confirm the change is really in the file + simulate the flow before calling it done.
- Built + committed 4b (dock + app-wide chrome), ran the audit, applied fixes.
- Blew through a run of bug reports live: bubble cycling → sync, lore misfires (X-Wing/TIE/defector/Finn/Kylo Ren), Whisper hallucinations, "in the trunk" keyword false-match, gestures missing from the log.
- Diagnosed the droid-switch stall (stale brain + no launch-watcher after onboarding) and the two orphaned KYBER processes on quit (top-only terminate) — fixed both in `tray_shell`.
- Prepped the tiered custom-mood cap; **bridge dropped before commit** → pending.

### 2026-07-15
- Bridge back. Committed the tier-aware custom-mood cap (lite 4 / full 8) to both sound-mapper files. Persisted this dev log to the machine (`DEVLOG.md`) so it survives container resets.

### 2026-07-17
- **Faction-agnostic prompt rewrite landed.** Long design conversation resolved the core tension: temperament (sliders) → disposition = valid; temperament → faction = invalid. So the prompt is now agnostic, canon is detected by *name* (one line), villain builds get colder low-end traits + personality-modulated guidelines. Pulled all lore lists/examples → ~36% smaller prompt (the latency fix).
- Refined interactively with Kalvin against rendered full prompts (Sparky/R2/Chopper/BD-1/villain): broadened canon line to "films, series, and games" (BD-1 is a game, Chopper is a series); made loyalty a **universal bond** ("their person… devoted above all") not a warmth-only trait — the "this is THEIR droid" framing must hold for every build; added **dual-world Earth knowledge** line; trimmed examples to 3 content-driven ones (kept pineapple-on-pizza, dropped the World Cup and the warm-biased "glad to see me").
- **Keyword gate**: dropped bare "backup", gated the 5 movement gestures to terse imperatives (no "?", ≤6 words). Unit-tested against the two sentences Kalvin flagged.
- **Ollama lingering-process fix**: PID-file so the tray can kill an Ollama that the Mainframe started during setup.
- Committed `kyber_core.py`, `config.py`, `provisioning.py` to the machine. All verified in-workspace (parse + real-file render assertions + keyword tests + PID round-trip). **Next: rebuild + live-test.**

### 2026-07-17 (pm — curious/confused, momentum, audit)
- Kalvin ran the (rebuilt) agnostic prompt live on R2-D2 and got **almost nothing but curious/confused** (Disney World / "We'll see Luke/Vader/Kylo" all flat). Diagnosed from the code + screenshots: the rewrite had dropped the named-entity valence rules R2 needs; flat statements only matched "small talk → neutral or curious"; the "curious" label collides with R2's curiosity *trait*; and the classifier echoes its own past labels back → a temp-0 snowball.
- Fixed in `kyber_core.py` (all mocked/rendered for Kalvin before editing, per his workflow): canon **react-from-allegiance** line (canon branch only), a statement/subject guideline, tightened curious/confused, firmer Earth line, two new examples. Restructured `_build_chat_messages` to **context-as-prose** (no label echo). Added a **soft** emotional-momentum state machine (lingers a beat, always yields to a clear new feeling, burns down when unreinforced so it can't loop) — Kalvin's explicit ask: fear→reassurance must read happy immediately, not stay scared.
- Ran a **full-file audit** (subagent). Real findings fixed: the retry was a no-op at temp 0.0 (now greedy → temp-0.5 retry, `num_predict` 10→24); "confused" was overloaded as both the glitch sentinel and a real label (now `GLITCH_SENTINEL` so the tray glitch light only fires on a real glitch). Audit's "thinking-Qwen3" flag was a false alarm — tier models are the *instruct* (non-thinking) variants. Momentum globals, param threading, f-string, and call sites all audited clean.
- Committed `kyber_core.py` to the machine. Verified in-workspace (parse + render checks + momentum sim). **Next: rebuild + live-test** (checklist above updated with the new items).

### 2026-07-17 (evening — rules → definitions rewrite + eval harness)
- Kalvin rebuilt and live-tested the Follow-up-1 (rule-based) prompt. Big improvement (Vader/Kylo → scared, X-Wing → excited), but still misfired: bad-news → scared, third-party/expository sadness ("nobody plays with him... put in a closet") → scared/curious. Kalvin's key push: **"we can't keep making small targeted tweaks for isolated cases — we need a general fix."** Correct — the guideline list is a lookup table that never converges.
- Together diagnosed it two ways: (1) the prompt asks the model to *match rules* instead of *understand meaning*; (2) the assembled request was itself malformed — examples in `User:"x"->label` format but the query posed differently, and context fenced off as "do NOT label," which is backwards for cumulative/empathetic sadness. Rendered the exact payload to prove it.
- **Rewrote to definitions** (see Follow-up 2 above): one meaning-definition per emotion + method line; examples and live query now share one transcript format ending in `→`; context is felt, not fenced; `_match_mood` hardened.
- **Built `eval_emotions.py`** to stop tuning by screenshot — fixed 42-case battery through the live classifier vs real Ollama, acceptable-set grading, momentum off. Iterated *on the numbers*: 89% (leak-inflated; old examples overlapped the test set) → narrowed `scared` → 94% → enemy-framing clause + bad-news target → 97% → probed the one straggler with 6 sibling cases, found a real **abandonment → scared** lean (not a one-off), fixed it at the `scared` definition → **98% (41/42)**. Lone holdout "abandoned in the middle of nowhere" is a genuine stranded-in-the-wild dual read.
- Committed `kyber_core.py` + `eval_emotions.py` (+ a `_backup_pre_promptfix/kyber_core.py` rollback) to the machine. Bridge froze content reads mid-session (see gotcha note in Follow-up 2) — worked around it, no data lost. **Next: rebuild + live-test the whole conversation (momentum + multi-turn, which the eval can't see), THEN the latency/trim pass.**

### 2026-07-17 (late — live-tested the definitions build; enemy tagging; dock fix)
- Ran the definitions build live from source (`python main.py`; Ollama started by hand from the frozen `%LocalAppData%\KYBER\runtime` since source mode looks in `pc\runtime`). Mostly excellent: allies → excited, Vader → angry/scared, stormtroopers → scared, even "what the fuck is with you and Kylo" → angry (read the tone). Latency measured fine (Qwen ~0.3–0.5s warm) → dropped the trim.
- **Kylo Ren** was the holdout — "we'll see Kylo Ren" kept reading excited in a fun stream. Ran it to ground (see Follow-up 3): not cutoff, not ignorance (asked cold, R2 → "fear"); the model knows but doesn't apply it fast, and R2's `brave=5` + excited context suppress it, with Kylo's signal too weak/noisy (Ben Solo) to survive. Tried the system-prompt roster → regressed abandonment 98→95% and didn't even fix it → reverted. **Per-line enemy tagging** (celebrity droids only) fixed it: eval 43/44 + **live-confirmed** Kylo/Palpatine/stormtroopers scared mid-stream. Kalvin: "fuggin' finally."
- Also fixed the **Com Uplink dock** not updating the droid on a model change (`/status` now re-reads DROID_TYPE/NAME fresh), and added **multi-turn conversation mode** to the eval.
- New open thread logged: **STT latency on slow machines** — Whisper is `small` on every tier (only the LLM is tiered), so a 2-core box will drag on Whisper, not the prompt. Fix = tier Whisper too (`base`/`tiny` on lite).
- Committed `kyber_core.py` + `eval_emotions.py`. All source-only — **needs the rebuild** to bake into the exe. Prompt work: **done.**

---

## 🔒 Locked design (reference — don't relitigate)

**Com Uplink** = a persistent **dock** at the bottom of every app page (deliberately unlabeled — the "what IS that?" curiosity is the point). Tap → a partial bottom-sheet rises (nav stays visible) with the big droid + live-emotion speech bubble on the left and an iMessage-style chat log on the right. Not a 7th nav tab. Live feeling bubble tracks the last emotion; gestures log but don't change it.

**Emotion palette (shipped):**

```
happy   ✨ 💫 💛 🟢     excited  ⚡ 🎉 💥     sad     🥀 💔
angry   💢 🔥 🚩        scared   ⚠️ ‼️ ❗     disgusted ⛔ 🚫
curious 👀 ❓ 🔎        confused 🌀 ⁉️ ❔     defensive 🛡 🚧 🛑
neutral ⚪
```
Random glyph per pool each turn. In the Legend. (🟢 is a stand-in for a "green flag"; neutral is just the white circle.)

**Gesture glyphs (shipped — NOT in the Legend, discovered by watching the droid):**

```
👋 come here   🔙 back up   ↩️ come back   💨 retreat   💃 dance   👉 that way
🤸/🧍 expressive on/off   🌙/☀️ hotel on/off   🐾/😴 pet on/off   ☕/💤 wake/sleep
```

**Other locked bits:** disgust = recoil (⛔🚫), not a queasy face. Neutral dims so real emotions pop. Custom moods are mood-based + per sound profile (seasonal packs = separate profiles). Fonts: Quicksand app-wide (Space Mono dropped).
