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

from bleak import BleakScanner
from droiddepot.connection import DroidConnection
from droiddepot.protocol import DisneyBLEManufacturerId
from droiddepot.script import DroidScriptEngine, DroidScripts

from config import PROJECT_DIR

RESULT_PATH = os.path.join(PROJECT_DIR, "activation_result.json")
DISCOVERY_TIMEOUT_SECONDS = 20


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

    try:
        droid = await discover_and_connect()
        if droid is None:
            write_result("failed", error="Droid not found on reconnect")
            return

        await droid.connect()

        try:
            await DroidScriptEngine(droid).execute_script(DroidScripts.DroidBayActivationSequence)
            write_result("done")
        except Exception as e:
            write_result("failed", error=f"Activation script failed: {e}")
        finally:
            await droid.disconnect()

    except Exception as e:
        write_result("failed", error=str(e))


if __name__ == "__main__":
    asyncio.run(main())
