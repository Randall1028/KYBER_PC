# KYBER (Windows Edition)

**K**inetic **Y**ammering and **B**ehavioral **E**ngine **R**outines. A local, fully offline conversational AI brain for Galaxy's Edge Droid Depot droids, running on Windows.

KYBER turns your Droid Depot droid into a voice-driven companion: it listens, talks back with a personality you shape, and reacts with real motion and sound, all running locally on your PC with **no cloud accounts and no API keys.**

This is a hobbyist build for the *Star Wars* fan/maker community. It's free, and it's meant to stay that way (see [License](#license)).

> ⚠️ **Beta (v0.85.5).** The Windows edition is a fresh port, now in beta testing. It runs end to end, but you may hit rough edges, so bug reports are welcome (see [Feedback & bugs](#feedback--bugs)).

---

## What it does

- **Real conversation:** a persistent voice pipeline (wake-free listening, voice-activity detection, local speech-to-text, and a local LLM that produces both the reply and a live emotion read) drives everything else.
- **A personality you control:** five trait sliders (brave, curious, sassy, playful, sensitive) shape how your droid talks, independent of which sound bank it plays from.
- **Built-in character personalities:** ready-to-use profiles for **R2-D2, BB-8, Chopper, and BD-1**, tuned to match each character, plus five blank neutral slots you can build out yourself.
- **Chassis-aware movement:** R, BB, C, A, and BD chassis each get appropriately scaled motor behavior for every gesture.
- **A calibration wizard:** a user-feedback method for tuning gesture timing, so the droid can compensate for lower battery levels.
- **Autonomous modes**, triggered by voice (see [Voice commands](#talking-to-your-droid-voice-commands-beta) below):
  - **Pet Entertainer:** fast, erratic movement bursts to give a cat or dog something to chase.
  - **Expressive Mode:** more animated, frequent gesture movement during conversation.
  - **Hotel Sentry:** a small, deliberate movement roughly every 15 minutes for up to 8 hours, timed to keep a hotel room's motion-sensing AC/lights from timing out overnight. While active it ignores everything it hears except the *"off duty"* command.
- **Beacon Relay:** scans for other droids and official Disney location beacons and rebroadcasts your droid's presence so nearby detectors and droids can see it.

---

## Talking to your droid: voice commands (beta)

> **Heads-up:** think of these as beta shortcuts. In the full release, KYBER's on-board language model will pick up most of this from natural conversation. For now, here are the exact phrases that reliably drive each behavior.

Matching is loose: the phrase just has to appear *somewhere* in what you say, so "okay, you're on duty now" works the same as a bare "you're on duty."

**Hotel Sentry:** periodic movement to keep a hotel room's motion-sensing AC/lights awake

- On: *"you're on duty"* · *"activate hotel mode"* · *"start hotel mode"*
- Off: *"you're off duty"* · *"end hotel mode"* · *"deactivate hotel mode"*
- While it's running, it ignores everything else you say until you turn it off.

**Pet Entertainer:** fast, erratic movement to give a pet something to chase

- On: *"go play"* · *"activate pet mode"*
- Off: *"end pet mode"* · *"stop pet mode"*

**Expressive Mode:** more animated body movement during normal conversation

- On: *"move around"* · *"roll around"* · *"start expressive mode"*
- Off: *"stop moving"* · *"end expressive mode"* · *"deactivate expressive mode"*

**Quick moves** *(only while Expressive Mode is on)*

- Come toward you: *"come here"*
- Back away: *"back up"*
- One to expect: while Expressive Mode is on, an excited exclamation might set the droid off into a happy dance, for example *"let's go!"*.

---

## Requirements

- **Windows 10 or 11, 64-bit**
- **8 GB of system RAM** or more
- A dedicated **GPU (NVIDIA or AMD Radeon)** with about **4 GB of video memory (VRAM)** is recommended for fast responses. Without a GPU, KYBER falls back to your CPU, which works but is slow.
- About **5 GB of free disk space**, plus a **broadband connection** for the one-time first-run download
- A **microphone**
- A **Galaxy's Edge Droid Depot droid** (any RC-style unit), powered on and nearby

---

## Installing

1. Download the latest **`KYBER_Setup_x.x.x.exe`** from the [**Releases**](../../releases) page.
2. Run it. Because this is a small hobbyist project and the installer isn't code-signed, Windows SmartScreen may warn *"Windows protected your PC."* Click **More info → Run anyway**. (Your antivirus may also flag it; that's a false positive from the unsigned installer.)
3. Choose where to install and finish the wizard.
4. Launch KYBER. On first run it shows the **first-run setup screen** and downloads the AI components (about 5 GB). This is a one-time setup, so leave the window open until it finishes. Everything lands in your user folder (`%LocalAppData%\KYBER`), not in the install directory.
5. It then walks you through onboarding: pairing ("claiming") your droid over Bluetooth, activating it, a quick mic check, and choosing its personality.

Once the wizard finishes, your droid is listening. Say hi.

---

## Known limitations (beta)

- **Droid won't connect during claim/activation:** on some PCs the droid shows up in the claim list, but pressing **Activate** stalls or comes back *"not found"* — likely a weak or wedged Bluetooth radio choking on that first connection. A workaround that often frees it up: open **Windows Settings → Bluetooth & devices**, find your droid in the device list, and **click it once**. You do *not* need to pair it — just clicking is enough to nudge Windows into opening the connection — then go back to KYBER and try again. Fair warning: it's finicky and not fully reliable, so you may need a couple of attempts. If your PC's built-in Bluetooth seems to be the culprit, a USB Bluetooth 5.0 adapter may be worth trying as a more stable alternative, though that hasn't been confirmed yet.
- **Gesture precision:** gestures have been dialed in as carefully as possible, but Droid Depot droids are mechanically imprecise, so mistimings can and will happen. A 180° spin might overshoot on one attempt and undershoot on the next. Give the droid some leeway here: KYBER is built for lively, in-character movement, not robotic precision.
- **Unsigned installer:** expect the SmartScreen / antivirus warnings noted above until the project is code-signed.
- **WebView2:** the UI uses Microsoft's WebView2 runtime, built into Windows 11 and most updated Windows 10 installs. If the window comes up blank, install the [WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/) and relaunch.
- **Microphone:** KYBER uses your Windows *default* input device; set the mic you want as the default in Windows Sound settings.
- **Antivirus can block the microphone:** some security software silently blocks KYBER's mic even when it's white-listed. If Windows says the mic is connected to KYBER and nothing is being heard, check all possible settings in your security software to determine what's blocking it (ex: Norton's SafeCam seems to block it).
- Microphone, networking, and Bluetooth behavior ultimately live in Windows's hands and can vary machine to machine.

---

## Feedback & bugs

This is a beta, and real-world reports are exactly what it needs. Please open an [**Issue**](../../issues) for anything broken or confusing. It helps a lot to include:

- your **Windows version** (10 or 11),
- **how much video memory (VRAM) your GPU has**, if any,
- your **droid model** (R2-D2, BB-8, etc.), and
- if it's a crash, **whatever the on-screen error said**.

---

## Disclaimer

KYBER is an **unofficial, non-commercial fan project**. It is **not affiliated with, authorized by, endorsed by, or sponsored by** The Walt Disney Company, Lucasfilm Ltd., or any of their affiliates. *Star Wars*, *R2-D2*, *BB-8*, *Chopper*, *BD-1*, *Droid Depot*, *Galaxy's Edge*, and all related names, marks, and imagery are trademarks of their respective owners, referenced here only to describe hardware compatibility. Use this software with your own hardware at your own risk.

---

## License

Released under the [MIT License](LICENSE): free to use, modify, and share.

KYBER is free because building droids should be fun and accessible, not a revenue stream. If you build something with it, please credit everyone involved, and honestly, hearing about it would make my day.

---

## Acknowledgments

KYBER stands on a lot of other people's work, and it wouldn't exist without any of them.

The Droid Depot Bluetooth protocol (the entire reason any of this is possible) was originally reverse-engineered and documented publicly by **Baptiste Laget**, who worked out the droids' BLE service, motor commands, and sound banks. Everything KYBER does to a droid traces back to that groundwork.

That protocol knowledge reaches KYBER through **[`pyDroidDepot`](https://github.com/thetestgame/pyDroidDepot)** and **[`pydBeacon`](https://github.com/thetestgame/pydBeacon)**, both written by **Jordan Maxwell** ([thetestgame](https://github.com/thetestgame)), who turned it into usable Python.

The brains are all open, local models and runtimes: **[Qwen3](https://ollama.com/library/qwen3)** (the language model, from the Qwen team at Alibaba), **[Whisper](https://github.com/openai/whisper)** (speech-to-text, from OpenAI) via **[`faster-whisper`](https://github.com/SYSTRAN/faster-whisper)**, and **[Ollama](https://ollama.com)**, which runs the model locally.

And the app itself is held together by **[`bleak`](https://github.com/hbldh/bleak)** (Bluetooth), **[`pywebview`](https://pywebview.flowrl.com/)** (the UI window), and **[`pystray`](https://github.com/moses-palmer/pystray)** (the system tray).
