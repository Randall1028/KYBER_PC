"""
claim_connect_worker.py -- connects to ONE specific droid chosen from
claim_worker.py's scan results, verifies the connection, saves its MAC,
disconnects. Separate process for the same reason claim_worker.py and
activation_worker.py are: keeps bleak/winrt out of kyber_config_server.py's
own process entirely.

Takes the target MAC as a command-line argument and does a fresh short
scan filtered to that one address, rather than trying to connect purely
by MAC with no live BleakDevice/advertisement data in hand -- that
MAC-only path has never been verified against pyDroidDepot's actual
DroidConnection constructor requirements (see the handoff notes on this),
so this deliberately reuses the one scanning approach already proven to
work, just filtered to a single target instead of taking the first match.

Writes its result to claim_connect_result.json in the same folder, which
kyber_config_server.py's /setup/claim_connect route waits on synchronously
(this whole process is quick -- typically a few seconds based on testing
so far -- so there's no need for a separate polling endpoint the way
Activation/Ready need one for their much longer waits).

Run directly (normally launched by the Mainframe server, not by hand):
    python claim_connect_worker.py AA:BB:CC:DD:EE:FF
"""

import asyncio
import json
import os
import sys
import time
import traceback

from bleak import BleakScanner
from droiddepot.connection import DroidConnection
from droiddepot.protocol import DisneyBLEManufacturerId

from config import PROJECT_DIR, update_env_values

RESULT_PATH = os.path.join(PROJECT_DIR, "claim_connect_result.json")
DISCOVERY_TIMEOUT_SECONDS = 15


def write_result(status: str, error: str = ""):
    with open(RESULT_PATH, "w") as f:
        json.dump({"status": status, "error": error}, f)


async def find_and_connect(target_mac: str):
    target_mac = target_mac.upper()
    async with BleakScanner() as scanner:
        deadline = time.time() + DISCOVERY_TIMEOUT_SECONDS
        while time.time() < deadline:
            devices = scanner.discovered_devices_and_advertisement_data
            for addr, (ble_device, adv_data) in devices.items():
                if addr.upper() != target_mac:
                    continue
                mfr = adv_data.manufacturer_data or {}
                if DisneyBLEManufacturerId.DroidManufacturerId in mfr:
                    return DroidConnection(ble_device.address, mfr)
            await asyncio.sleep(1)
    return None


async def main(target_mac=None):
    # target_mac may be passed in directly (frozen --mode dispatch) or read
    # from argv (running this script directly as before).
    if target_mac is None and len(sys.argv) >= 2:
        target_mac = sys.argv[1]
    if not target_mac:
        write_result("not_found", error="No MAC address given")
        return
    print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Worker started, looking for {target_mac}...", flush=True)
    write_result("connecting")

    try:
        droid = await find_and_connect(target_mac)
        if droid is None:
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] {target_mac} not seen during rescan.", flush=True)
            write_result("not_found", error=f"{target_mac} not seen during rescan")
            return

        print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Connecting to {target_mac}...", flush=True)
        # silent=True -- same reasoning as Pi's real code: the library's own
        # automatic "paired!" chirp+light cue would clash with Disney's own
        # activation show, which is coming up shortly after on the
        # Activation page. This is the claim moment specifically, one of
        # only two places Pi silences this on purpose.
        await droid.connect(silent=True)
        print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Connected successfully.", flush=True)

        update_env_values({"DROID_MAC": target_mac})
        write_result("found")

        # Disconnect in its OWN try/except: the claim already succeeded and
        # "found" is already written, so a hiccup tearing the link down must
        # never fall through to the outer except and overwrite it with
        # "not_found" (that was the bug -- a good claim reported as failed).
        try:
            await droid.disconnect()
        except Exception:
            print("[CLAIM_CONNECT] Post-claim disconnect hiccup (ignored).", flush=True)
            traceback.print_exc()

    except Exception as e:
        print("[CLAIM_CONNECT] Exception during connect:", flush=True)
        traceback.print_exc()
        write_result("not_found", error=str(e))


if __name__ == "__main__":
    asyncio.run(main())
