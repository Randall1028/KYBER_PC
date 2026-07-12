"""
tray_shell.py -- the real tray + window shell, evolved from
tray_shell_test.py now that there's an actual Mainframe (kyber_config_server.py)
and brain (kyber_core.py) to wrap instead of placeholder HTML.

Two modes:
  "onboarding" -- ONBOARDING_COMPLETE isn't set in .env yet. Launches the
                  Mainframe (kyber_config_server.py) as a subprocess, shows
                  the window pointed at its real wizard (starting at
                  /setup/welcome), and watches .env in the background for
                  ONBOARDING_COMPLETE to flip on.
  "running"   -- ONBOARDING_COMPLETE is already set (either it just flipped,
                  or this is a later launch after setup was already done).
                  Launches kyber_core.py directly and polls its live status
                  endpoint (see kyber_core.py's own module docstring on the
                  status server) to drive the tray icon automatically --
                  replacing the old test file's manual "Set: Idle/Listening/
                  Glitched" menu items with the real thing.

Mic ownership handoff: kyber_config_server.py's own /setup/finish route
(not this file) is responsible for releasing its mic stream before
ONBOARDING_COMPLETE gets set -- see that route's comments. This file just
watches for the flag; it doesn't need to coordinate the mic itself.

Icon images are the real KYBER hexagon logo (the same LOGO_SVG used
throughout kyber_config_server.py's pages), rasterized once and tinted per
state -- idle=muted blue-gray, glitched=red, both a flat recolor of the
mark; listening keeps the logo's actual two-tone gold+blue brand colors
unmodified, since "listening" is the droid's ordinary active state. Swapped
in as three base64 PNGs baked in at build time (see _ICON_*_B64 below)
rather than adding a runtime SVG-rasterization dependency (cairosvg/etc
need a native Cairo install, exactly the kind of Windows dependency pain
this project has hit before with bluezero/dbus-python) -- this stays a
pure asset change with zero new pip requirements.

Windows-specific shutdown note: kyber_core.py spawns its own two
multiprocessing children (BLE + Whisper). A plain Popen.terminate() on
Windows calls TerminateProcess(), which does NOT give kyber_core.py's own
KeyboardInterrupt handler a chance to run -- meaning its ble_proc/
whisper_proc could be orphaned, and the droid connection wouldn't get a
clean disconnect(). Instead, kyber_core.py is launched with
CREATE_NEW_PROCESS_GROUP, and stopped by sending CTRL_BREAK_EVENT to that
group -- Windows delivers that as a real KeyboardInterrupt to processes in
the group, which is exactly what kyber_core.py's existing
`except KeyboardInterrupt` teardown (disconnect + terminate both children)
is already written to handle. Not yet verified on real hardware.

Run directly:
    python tray_shell.py
"""

from threading import Thread
import base64
import io
import os
import signal
import subprocess
import sys
import time

import requests
import webview
from PIL import Image
from pystray import Icon, Menu, MenuItem

from config import ENV_PATH, PROJECT_DIR, relaunch_command

import provisioning

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_SERVER_PATH = os.path.join(THIS_DIR, "kyber_config_server.py")
KYBER_CORE_PATH = os.path.join(THIS_DIR, "kyber_core.py")

MAINFRAME_PORT = 5001
KYBER_CORE_STATUS_PORT = 5010  # must match kyber_core.py's own STATUS_PORT

STATUS_POLL_INTERVAL = 1.0
ONBOARDING_FLAG_POLL_INTERVAL = 1.0


def _load_icon(b64_png: str) -> Image.Image:
    """Decodes one of the baked-in base64 PNGs below into a PIL Image pystray
    can use directly. The PNGs are the new KYBER crystal mark (a solid
    stencil-cut silhouette, not thin separate line strokes like the old
    hexagon logo used to be) -- rasterized once and recolored per tray
    state using a Jedi-lightsaber trio: blue for idle, green for listening,
    red for glitched. The old hexagon-logo icons fragmented into disconnected
    specks at real Windows tray size (16px) and didn't read as distinct
    states; this shape stays a single solid blob down to 16px. See the
    module docstring for why this is baked-in data rather than an SVG
    rasterized at runtime."""
    return Image.open(io.BytesIO(base64.b64decode(b64_png))).convert("RGBA")


# 64x64 PNG, KYBER crystal mark filled #00a8ff (brand blue -- idle)
_ICON_IDLE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAABmJLR0QA/wD/AP+gvaeTAAAIVElEQVR4nNXbe3BdRR0H8M/epJQWpNaqpaDIW9HxAQ6CDArIoMDYKVNomj60qAwFHzykgygVKg8tRQXHUdEqODI0N1EGSwV5FIdxQAeQ2toHxGKhgECHZxvaQtKc9Y97kt7b5N7cJOde4ncmk7O7v/3t737PPn772z3BW4G2OMYOx8rZoDn85y2xIUWoa2s3xd2NcTkuwO44TfCqRM6McH9dbUmRq1tLLfEoYzyKSxR+fA/GCZZrjT/RFnermz0p6kPAkniy4EF8sIxEg+g8vMuSOLEuNqVorKn2fDxazksSk9BQVZ3gXPl4pOgcM8IzNbVPrXpAWxyjNV6LB0TFbzQRPF+FhlMFa7TGc8RY03mqNgRE80Xz7PrWgybTwz6iS6vQspfoF1r9xS3xwJrYKUsCCm/9x26LbxfLDK3gERA9gs26rRM8hs0VNB+vwd3a4pjMbC1CNgTk49ESq0QX6qqgM3Fl2uoVWG5WWK8pPCG4d4AWDpZYIMagJb43E5tTZDUJ/gCHDCgVTEqfJokO0RI/BRInVOGRfFOrVsGRWuIEe1vkhLBjGDYjix5wZxyNowddL5gg+Gv6N6GKGo1YbLMbMcUmD2uLHxt0u7tg+AR0OEqpY1NLHGG8sYIHcLjE4uEqzGIO+HQGOoaGYL/hqsiagC6dduCNIWvL2Va1bBz+Xmb4BMQi9zZYaHbYYg/XoGPQurp9RnA9/lRlje2DbmMXZEHAE0WpPUGHsUPSHdwommwvZ+DPVdQYAQQEa3ufowu0xPvlrMQefWSj4+XjKyi3ljeKWmxxsu2mVuEfjAACWFP0HATHYd8ysqMwfoB2R6HN7k401mn4b5H2263TIWpBIo4EAop7wNDxkOAabEzTuwlutdUSxWQm7rMgJNqtEL0qN3wChu8JjrJW57A0bJSYYmbYJMZvy/ukoBnTMKVEMrhYPm7DMZhgRAyBqeHlKre4/WELPi9nT/n4lLxb8E458+S8R3ASfoNXU/l9sRhfAnEQS2YZZLMZikMaBjskpmsOa+RMwN6CGYKlEi9I/DK1cK6cvUVT0rG/tUjHi8M1Pavt8JqBRfrgfDPDXaApPKwQK+zBeHxFdK/EsxI/1OBFzWbpNBEzBLeLnh6u4dlEW/LxLMr45cGxokNxY1Hu9ZrDhSVyy+JYr3u6aGN0E17BRUVSTyGPvOawKgvTa9EDXqo4J0R/lzOvT/7ksE1wQ1HO/XKW7SK1v0JPWSkf11kSPzJ0kwvIhoAd1iFigzEOFOyPR8u0uFxT6O63LPHAIFo9TM7YQVrajzlZYHbYgmew0ZTQoSl04g6QyGGFnr1B4l9l9SSDnEuiF4ZkbxGyDIquwQnycbHWODkNe90i+JHRNgpOQYdE+bE7Ozxr55JXDp9L3/xSDTYN1+gsCehZCs8S/VE0R84c/Nub7rGbtfis9QY6C1xdoazDdPdqCttFLZrCCHCEdqK4++ZEi/uQMNrjFoSkopbQS8Bm3bYozC0FxLSN1niqnDOzMDo7AnJ9nKG+JHRWdR6wBg9pDkvNCP8U/T7NTzT4hryPi5aJw58AyfJobIzHbJUoJbVAQuFpjh0+VIWm1ZiQngN06rZPmv9r06zQ6m9pG8Me/2TZAyaHbdjQbxs9PWFmKL8C9KDLahwksV6iXXAsXpG4VN4cPRHoYH0WZmd9NFZuT1AgIR8/OqCGwpK6UWHjcxCILtOoS7CwSHJEeYI96G8d7wmQ5iSqPcgoXglWaXCD6HKKDlrDSCRg1+BIcJ3pxopmgUavValnddHzHdhT9PUiia2UxCKHjKwJ2LUHLBdCFN2Tpqtdt0v1vGG0QqhsZ3k5d3qQyHoItKOrNxVdlrYyH9Izg4ERKzpDZDT+yZqAwh5g5+wcHFDyv9HPtcV3DKhns3YqBtpGKAEFVIoOzZJYpzWe3m/pzXGS1jjf3NCFx8tqyVXYUA0S2RMwcJR4ougP8vFWbXFnxDcfv2iUtaJ5Ygxi2R8ZdWZHQC0uSVW7pZ0qcaJ8vFpwkuik3pK891XQ82TqK2SC7AnotnYQ/WocFhVtdwrI+TBW98kvYOWQbesH2Q+BSZ7Q4/xEoyyIOZUntP6wXVLGZwjZdX9qQUDh2kp7mhrvMGeLFqHadXuFJvfh/H5L40jvAQXsHL/RQjnP4adV1Yy+p9UHBGek6VLfIcMVoKCuFihdCcZJ/Eyn+Qph7UpYqdky0XdS27aLbjKq9w7SFtMG1DEo1IaApM9SOMUop0icW7FecJVWB6Zng7DQOM8Lrk3Tq4TQ/9Q4RNSKgMIQiF4WfBfL5FyXngTdjUcE36JkT7/WY27DpQqr0+tyrrXZRTgUmU+A1IqA9Z7CVsFVpoerTDRV7G3rJd2aTQ+LJM7prRNc7f32w+w05w1NYbvgE0WaM3OBe1AbAgqBz3U4AjzvYOzVW97ocBDSctoFbYJLlO76SpFRDKAYtfxe4EF8wdL4Ng3OVnpx+qsoXI0v4Ptp7O/MCvqeUzlkPiTUjoBu1yveGvfc/uh7orvBREsEF2N0GW1bMTmLc4BdUTsCZoWNopt708EVoMGVSp2i/WzyW5xVRlOnaKbmsKIWZtb2k5kGi7yZevTRcWIMEscrHQ6NmKXcdduc88wIt9fKxNoS0BTaNYXX09RcrTpwZ5W1X0t1DPsAtBLq99VYAX3vDvaPJ3U7taaWpKjtR1NDw9NyjtFc2zffg/r0gCCPN6uQ3C46vdbdvhj1IaBwCeq8AaQivmxG+EcdLOpF/eaA5vCr3oPS/vF9zSFfN3tS1HcO2OJrxvXz9Whwl+DyutqSor6rwNzQpcs0seQWWbugOauTnv8P/C6+G+Tj2driAW+lKf8DYEGJn/0Sru8AAAAASUVORK5CYII="
)

# 64x64 PNG, KYBER crystal mark filled #2ecc71 (app's existing --success green -- listening)
_ICON_LISTENING_B64 = (
    "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"
)

# 64x64 PNG, KYBER crystal mark filled #e63946 (app's existing --error red -- glitched)
_ICON_GLITCHED_B64 = (
    "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"
)

ICON_IDLE = _load_icon(_ICON_IDLE_B64)
ICON_LISTENING = _load_icon(_ICON_LISTENING_B64)
ICON_GLITCHED = _load_icon(_ICON_GLITCHED_B64)

window = None
icon = None

config_server_proc = None
kyber_core_proc = None

mode = "onboarding"  # or "running", flips once ONBOARDING_COMPLETE is seen
_shutting_down = False


class OnboardingAPI:
    """Exposed to the onboarding window's JS as window.pywebview.api --
    the Ready page's Continue button calls close_onboarding_window() once
    the live 'say hi' test confirms the brain is actually connected. This
    is the one thing a plain webpage can't do on its own: only the Python
    side can tell pywebview to hide the window, same as clicking its own
    close button already does."""

    def close_onboarding_window(self):
        if window is not None:
            # Hide FIRST, navigate LATER -- hiding doesn't destroy the page's
            # JS context, but load_url() does, and pywebview needs that
            # context intact to deliver this method's return value back to
            # the JS Promise that's awaiting it. Navigating before returning
            # was exactly what threw the JavascriptException: by the time
            # pywebview tried to resolve the Promise, the page it needed to
            # inject the result into had already been replaced.
            window.hide()

            def _navigate_once_hidden():
                time.sleep(0.5)  # let this method's return value actually
                                 # get delivered before we swap the content
                try:
                    window.load_url(f"http://127.0.0.1:{MAINFRAME_PORT}/")
                except Exception:
                    pass

            Thread(target=_navigate_once_hidden, daemon=True).start()
        return {"ok": True}

    def show_mainframe(self):
        """Used by Ready's Continue button now -- keeps the window visible
        and switches it straight to the Mainframe home page, instead of
        hiding into the tray. Same return-before-navigate ordering as
        close_onboarding_window() and for the same reason: navigating
        before this method returns would destroy the JS context pywebview
        needs to deliver the return value back to the calling page."""
        if window is not None:
            def _navigate_shortly():
                time.sleep(0.3)
                try:
                    window.load_url(f"http://127.0.0.1:{MAINFRAME_PORT}/")
                except Exception:
                    pass

            Thread(target=_navigate_shortly, daemon=True).start()
        return {"ok": True}


# ---------------------------------------------------------------------------
# Process lifecycle
# ---------------------------------------------------------------------------

def onboarding_already_complete() -> bool:
    from dotenv import dotenv_values
    return dotenv_values(ENV_PATH).get("ONBOARDING_COMPLETE") == "1"


def launch_config_server():
    global config_server_proc
    config_server_proc = subprocess.Popen(relaunch_command("mainframe"), cwd=PROJECT_DIR)


def stop_config_server():
    global config_server_proc
    if config_server_proc is not None and config_server_proc.poll() is None:
        config_server_proc.terminate()
    config_server_proc = None


def launch_kyber_core():
    global kyber_core_proc
    # KYBER supervises Ollama itself: make sure the bundled brain server is up
    # before kyber_core starts talking to it. Best-effort -- if the runtime
    # isn't installed yet (e.g. running from source before provisioning), log
    # and continue; kyber_core's own error handling covers an unreachable
    # server.
    try:
        provisioning.ensure_ollama_running()
    except Exception as e:
        print(f"[TRAY] Could not start Ollama: {e}", flush=True)
    # CREATE_NEW_PROCESS_GROUP -- see module docstring. Needed so a later
    # CTRL_BREAK_EVENT can target this process (and the children it spawns)
    # cleanly instead of a hard kill.
    kyber_core_proc = subprocess.Popen(
        relaunch_command("core"),
        cwd=PROJECT_DIR,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def stop_kyber_core():
    global kyber_core_proc
    if kyber_core_proc is None or kyber_core_proc.poll() is not None:
        kyber_core_proc = None
        return
    try:
        kyber_core_proc.send_signal(signal.CTRL_BREAK_EVENT)
        kyber_core_proc.wait(timeout=10)
    except Exception:
        # Didn't shut down cleanly in time -- fall back to a hard stop
        # rather than hang the tray app's own exit.
        try:
            kyber_core_proc.terminate()
        except Exception:
            pass
    kyber_core_proc = None


# ---------------------------------------------------------------------------
# Onboarding -> running handoff watcher
# ---------------------------------------------------------------------------

def brain_launch_requested() -> bool:
    from dotenv import dotenv_values
    return dotenv_values(ENV_PATH).get("BRAIN_LAUNCH_REQUESTED") == "1"


def watch_for_brain_launch_request():
    """Watches for BRAIN_LAUNCH_REQUESTED, set by the Activation page the
    moment its loading animation starts -- NOT ONBOARDING_COMPLETE, which
    still means something different (the whole wizard is actually done,
    set later by Ready's Continue button, and only checked again on a
    later full app relaunch to decide whether to skip the wizard
    entirely). The brain needs to start much earlier than "wizard done" now,
    since Pi's own real activation sequence fires as part of kyber_core.py's
    first connect, not a separate one-shot step."""
    global mode
    while mode == "onboarding" and not _shutting_down:
        if brain_launch_requested():
            from dotenv import set_key
            set_key(ENV_PATH, "BRAIN_LAUNCH_REQUESTED", "0")  # consume it --
                                                               # one-shot, same
                                                               # spirit as
                                                               # kyber_core.py's
                                                               # own flag
            mode = "running"
            launch_kyber_core()
            return
        time.sleep(ONBOARDING_FLAG_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Tray icon state, driven by kyber_core.py's live status once it's running
# ---------------------------------------------------------------------------

def poll_status_loop():
    current_state = "idle"
    while not _shutting_down:
        if mode != "running":
            time.sleep(STATUS_POLL_INTERVAL)
            continue
        try:
            resp = requests.get(f"http://127.0.0.1:{KYBER_CORE_STATUS_PORT}/status", timeout=1.5)
            data = resp.json()
        except Exception:
            data = {"connected": False, "listening": False, "glitched": False}

        if data.get("glitched"):
            new_state = "glitched"
        elif data.get("listening"):
            new_state = "listening"
        else:
            new_state = "idle"

        if new_state != current_state and icon is not None:
            current_state = new_state
            icon.icon = {"idle": ICON_IDLE, "listening": ICON_LISTENING, "glitched": ICON_GLITCHED}[new_state]
            icon.title = f"KYBER -- {new_state}"

        time.sleep(STATUS_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Tray menu + window
# ---------------------------------------------------------------------------

def on_open(icon, item):
    window.show()


def on_exit(icon, item):
    global _shutting_down
    _shutting_down = True
    icon.stop()
    stop_kyber_core()
    stop_config_server()
    provisioning.stop_ollama()
    window.destroy()
    # Same "can not completely exit" workaround tray_shell_test.py already
    # documented -- give WebView2/Chromium a moment to tear down before the
    # hard exit forces things regardless.
    time.sleep(0.3)
    os._exit(0)


def on_closing():
    Thread(target=window.hide, daemon=True).start()
    return False


def main():
    global window, icon, mode

    # Point Ollama + HuggingFace at the self-contained runtime folder before any
    # child process starts, so everything (this app, kyber_core, its Whisper
    # child) shares the same model store next to the exe.
    provisioning.configure_env()

    menu = Menu(
        MenuItem("Open Mainframe", on_open, default=True),
        Menu.SEPARATOR,
        MenuItem("Quit", on_exit),
    )

    icon = Icon("KYBER", ICON_IDLE, "KYBER -- starting up", menu=menu)
    Thread(target=icon.run, daemon=True).start()
    Thread(target=poll_status_loop, daemon=True).start()

    if onboarding_already_complete():
        mode = "running"
        launch_kyber_core()
        launch_config_server()
        # Safe to run both now -- kyber_config_server.py's own main() skips
        # starting its mic stream whenever ONBOARDING_COMPLETE is set, so
        # there's no device-contention risk with kyber_core.py's Whisper
        # process. Window starts hidden, pointed at the real home dashboard,
        # available via "Open Mainframe" whenever wanted.
        time.sleep(1)  # same startup-race buffer as the onboarding branch
        window = webview.create_window(
            "KYBER",
            url=f"http://127.0.0.1:{MAINFRAME_PORT}/",
            hidden=True, width=800, height=880,
            js_api=OnboardingAPI(),
        )
    else:
        launch_config_server()
        Thread(target=watch_for_brain_launch_request, daemon=True).start()
        time.sleep(1)  # give the Mainframe's HTTP server a moment to bind
                       # its port before pointing a window at it -- same
                       # fixed-sleep approach kyber_core.py's own mapper API
                       # startup already uses for the same kind of race
        window = webview.create_window(
            "KYBER Setup",
            url=f"http://127.0.0.1:{MAINFRAME_PORT}/setup/start",
            hidden=False, width=800, height=880,
            js_api=OnboardingAPI(),
        )

    window.events.closing += on_closing

    print("KYBER tray running. Right-click the tray icon for options.")
    webview.start()
    print("webview.start() returned -- app fully exited.")


if __name__ == "__main__":
    main()
