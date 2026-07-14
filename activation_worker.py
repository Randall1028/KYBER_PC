"""
activation_worker.py -- fires Disney's own DroidBayActivationSequence BLE
script for the wizard's Activation step, as a genuinely separate process
from kyber_config_server.py -- same reason claim_worker.py is one: bleak/
winrt sharing a process with this server's sounddevice-based Mic Check
reproduces the exact "Thread configured for Windows GUI but callbacks are
not working" conflict kyber_core.py's two-process split was built to end.

Scope: reconnect to the droid via a fresh scan (same approach as
claim_worker.py -- NOT a DROID_MAC-first connect. That path isn't
implemented anywhere yet; it hasn't been verified against pyDroidDepot's
actual constructor requirements, so this deliberately reuses the one
scanning approach we know works rather than introducing a second,
unverified one here), fire DroidBayActivationSequence over BLE, disconnect
cleanly, exit. Does NOT loop or hold the connection open afterward -- the
droid plays out its own light/sound/motor show independently over the
following ~30s, the same way Pi's kyber_core.py documents it, so this
process's job is just the one BLE write that kicks that off.

Writes its result to activation_result.json in the same folder, which
kyber_config_server.py polls from its /setup/activation_status endpoint --
same file-based polling pattern as claim_worker.py / claim_result.json.
Chosen deliberately over a live status check: PC's kyber_core.py isn't
running yet at this point in onboarding (unlike Pi, where kyber.service
is already running and exposes a live /droid_status API on port 5002 that
the real Mainframe polls instead -- confirmed by reading Pi's actual
kyber_core.py, not assumed).

Run directly (normally launched by the Mainframe server, not by hand):
    python activation_worker.py
"""

import asyncio
import json
import os
import time
import traceback

from bleak import BleakScanner
from droiddepot.connection import DroidConnection
from droiddepot.protocol import DisneyBLEManufacturerId
from droiddepot.script import DroidScriptEngine, DroidScripts

from config import PROJECT_DIR

RESULT_PATH = os.path.join(PROJECT_DIR, "activation_result.json")
DISCOVERY_TIMEOUT_SECONDS = 20
# Same flaky-BLE hardening as claim_connect_worker: bound each connect and
# retry, and catch CancelledError (a BaseException that "except Exception"
# misses) so a cancelled connect writes a result instead of freezing the page.
CONNECT_TIMEOUT_SECONDS = 20
CONNECT_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 2


def write_result(status: str, error: str = ""):
    with open(RESULT_PATH, "w") as f:
        json.dump({"status": status, "error": error}, f)


async def discover_and_connect():
    """Identical approach to claim_worker.py's discover_and_connect() --
    plain active scan filtered on Disney's real droid manufacturer ID, no
    MAC needed. See module docstring for why this doesn't switch to a
    MAC-first connect even though DROID_MAC is already known by this point
    in the wizard."""
    async with BleakScanner() as scanner:
        deadline = time.time() + DISCOVERY_TIMEOUT_SECONDS
        while time.time() < deadline:
            devices = scanner.discovered_devices_and_advertisement_data
            for addr, (ble_device, adv_data) in devices.items():
                mfr = adv_data.manufacturer_data or {}
                if DisneyBLEManufacturerId.DroidManufacturerId in mfr:
                    return DroidConnection(ble_device.address, mfr)
            await asyncio.sleep(1)
    return None


async def main():
    write_result("activating")

    last_error = "Could not connect to the droid to activate it"
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        droid = None
        try:
            print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] Connect attempt {attempt}/{CONNECT_ATTEMPTS}...", flush=True)
            droid = await discover_and_connect()
            if droid is None:
                last_error = "Droid not found on reconnect"
                print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] {last_error}", flush=True)
            else:
                await asyncio.wait_for(droid.connect(), timeout=CONNECT_TIMEOUT_SECONDS)
                try:
                    await DroidScriptEngine(droid).execute_script(DroidScripts.DroidBayActivationSequence)
                    write_result("done")
                    try:
                        await droid.disconnect()
                    except Exception:
                        pass
                    return
                except Exception as e:
                    last_error = f"Activation script failed: {e}"
                    print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] {last_error}", flush=True)
        except asyncio.TimeoutError:
            last_error = "Connection timed out"
            print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] Attempt {attempt} timed out after {CONNECT_TIMEOUT_SECONDS}s", flush=True)
        except asyncio.CancelledError:
            last_error = "Connection was cancelled"
            print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] Attempt {attempt} cancelled", flush=True)
        except Exception as e:
            last_error = str(e)
            print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] Attempt {attempt} error:", flush=True)
            traceback.print_exc()

        if droid is not None:
            try:
                await droid.disconnect()
            except Exception:
                pass
        if attempt < CONNECT_ATTEMPTS:
            await asyncio.sleep(RETRY_PAUSE_SECONDS)

    print(f"[{time.strftime('%H:%M:%S')}] [ACTIVATION] All {CONNECT_ATTEMPTS} attempts failed: {last_error}", flush=True)
    write_result("failed", error=last_error)


if __name__ == "__main__":
    asyncio.run(main())
