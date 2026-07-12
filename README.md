# KYBER — Windows Edition

**K**inetic **Y**ammering and **B**ehavioral **E**ngine **R**outines — a local, fully offline conversational AI brain for Galaxy's Edge Droid Depot droids, running on Windows.

KYBER turns your Droid Depot droid into a voice-driven companion: it listens, talks back with a personality you shape, and reacts with real motion and sound — all running locally on your PC, with **no cloud accounts, no API keys, and nothing to pay for.**

This is a hobbyist build for the *Star Wars* fan/maker community. It's free, and it's meant to stay that way — see [License](#license).

> ⚠️ **Beta (v0.85.1).** The Windows edition is a fresh port, now in public testing. It runs end to end, but you may hit rough edges — bug reports are welcome (see [Feedback & bugs](#feedback--bugs)).

> 🐧 Prefer to run KYBER off a Raspberry Pi tucked inside the droid instead? There's a **[Raspberry Pi version](https://github.com/Randall1028/kyber)** too. *(update this link to your Pi repo)*

---

## How the Windows edition differs from the Pi version

The Pi version runs on a Raspberry Pi inside the droid and talks to cloud AI services. **The Windows edition runs entirely on your PC and entirely offline:**

- **Local brain** — the language model ([Qwen3](https://ollama.com/library/qwen3)) and speech-to-text ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) run on your own machine via [Ollama](https://ollama.com). No Deepgram/OpenAI/Anthropic/Google accounts, no API keys, no per-use cost — nothing leaves your PC.
- **One-click install** — a normal Windows installer, not flashing an SD card and SSHing in.
- **Self-contained** — everything it needs downloads and tucks itself away on first launch, and a clean uninstall takes it all with it.

---

## What it does

- **Real conversation** — a persistent voice pipeline (wake-free listening, voice-activity detection, local speech-to-text, and a local LLM that produces both the reply and a live emotion read) drives everything else.
- **A personality you control** — five trait sliders (brave, curious, sassy, playful, sensitive) shape how your droid talks, independent of which sound bank it plays from.
- **Built-in character personalities** — ready-to-use profiles for **R2-D2, BB-8, Chopper, and BD-1**, tuned to match each character, plus five blank neutral slots you can build out yourself.
- **Chassis-aware movement** — R, BB, C, A, and BD chassis each get appropriately scaled motor behavior for every gesture. BB has its own dedicated movement set (it's a ball droid, not a wheeled biped); the others share the R-series gestures scaled to their own turning/driving characteristics.
- **A calibration wizard** — a short spin-test corrects for your specific unit's left/right motor balance so gestures land true.
- **Autonomous modes**, triggered by voice:
  - **Pet Entertainer** — fast, erratic movement bursts to give a cat or dog something to chase.
  - **Expressive Mode** — more animated, frequent gesture movement during conversation.
  - **Hotel Sentry** — a small, deliberate movement roughly every 15 minutes for up to 8 hours, timed to keep a hotel room's motion-sensing AC/lights from timing out overnight. While active it ignores everything it hears except the command to stop.
- **Beacon Relay** *(optional, on by default)* — scans for other droids and official Disney location beacons and rebroadcasts your droid's presence so nearby detectors and droids can see it.
- **Mainframe** — a browser-based control panel at **`http://localhost:5001`** for personality editing, sound-profile mapping, gestures, and motor calibration.

---

## Requirements

- **Windows 10 or 11, 64-bit**
- An **NVIDIA GPU is strongly recommended** — the language model runs much faster on GPU. It will fall back to CPU, but expect slow responses.
- **~6 GB free disk space** and a **broadband connection** for the one-time first-run download of the AI models
- A **microphone** (KYBER uses your Windows default input device)
- A **Galaxy's Edge Droid Depot droid** (R, BB, C, A, or BD series) with Bluetooth, powered on and nearby
- **No API keys or accounts** — nothing to sign up for

---

## Installing

1. Download the latest **`KYBER_Setup_x.x.x.exe`** from the [**Releases**](../../releases) page.
2. Run it. Because this is a small hobbyist project and the installer isn't code-signed, Windows SmartScreen may warn *"Windows protected your PC"* — click **More info → Run anyway**. (Your antivirus may also flag it; that's a false positive from the unsigned installer.)
3. Choose where to install and finish the wizard.
4. Launch KYBER. On first run it shows a **Core Upgrade** screen and downloads the AI components (~5 GB) — this is a one-time setup, so leave the window open until it completes. Everything lands in your user folder (`%LocalAppData%\KYBER`), not in the install directory.
5. It then walks you through onboarding: pairing ("claiming") your droid over Bluetooth, activating it, a quick mic check, and choosing its personality.

Once the wizard finishes, your droid is listening. Say hi. The **Mainframe** control panel is at **`http://localhost:5001`** whenever you want to tweak things.

---

## Known limitations (beta)

- **Gesture tuning** — only the **R-series** has been validated on real hardware. BB has its own dedicated movement set; **C, A, and BD** reuse the R gestures scaled for their chassis (and **C currently behaves identically to R**). All of these are wired up and won't crash, but the exact motion tuning for non-R chassis hasn't been confirmed on hardware yet.
- **Unsigned installer** — expect the SmartScreen / antivirus warnings noted above until the project is code-signed.
- **WebView2** — the UI uses Microsoft's WebView2 runtime, built into Windows 11 and most updated Windows 10 installs. If the window comes up blank, install the [WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/) and relaunch.
- **Microphone** — KYBER uses your Windows *default* input device; set the mic you want as the default in Windows Sound settings.
- Microphone, networking, and Bluetooth behavior ultimately live in Windows's hands and can vary machine to machine.

---

## Feedback & bugs

This is a beta, and real-world reports are exactly what it needs. Please open an [**Issue**](../../issues) for anything broken or confusing. It helps a lot to include:

- your **Windows version** (10 or 11),
- whether you have an **NVIDIA GPU**,
- your **droid model** (R2-D2, BB-8, etc.), and
- if it's a crash, **whatever the on-screen error said**.

---

## Disclaimer

KYBER is an **unofficial, non-commercial fan project**. It is **not affiliated with, authorized by, endorsed by, or sponsored by** The Walt Disney Company, Lucasfilm Ltd., or any of their affiliates. *Star Wars*, *R2-D2*, *BB-8*, *Chopper*, *BD-1*, *Droid Depot*, *Galaxy's Edge*, and all related names, marks, and imagery are trademarks of their respective owners, referenced here only to describe hardware compatibility. Use this software with your own hardware at your own risk.

---

## License

Released under the [MIT License](LICENSE) — free to use, modify, and share.

KYBER is free because building droids should be fun and accessible, not a revenue stream. If you build something with it, please credit everyone involved — and honestly, hearing about it would make my day.

---

## Acknowledgments

Built on top of [`bleak`](https://github.com/hbldh/bleak), [`pyDroidDepot`](https://pypi.org/project/pyDroidDepot/), [`Ollama`](https://ollama.com), [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper), [`pywebview`](https://pywebview.flowrl.com/), [`pystray`](https://github.com/moses-palmer/pystray), and [`pydBeacon`](https://pypi.org/project/pydBeacon/).
