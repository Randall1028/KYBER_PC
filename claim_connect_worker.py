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
# Bluetooth connects on marginal adapters can stall or get cancelled mid-way.
# Bound each attempt and retry a few times so a flaky first try doesn't leave
# the wizard hanging forever. CancelledError is a BaseException, so it must be
# caught explicitly -- a plain "except Exception" lets it escape and the worker
# dies without ever writing a result, which is exactly what freezes the page.
CONNECT_TIMEOUT_SECONDS = 20
CONNECT_ATTEMPTS = 2           # one solid retry; worst case ~50s, kept under
                               # the /setup/claim_connect route's subprocess
                               # timeout (see kyber_config_server.py) so the
                               # server never kills a retry mid-flight.
RETRY_PAUSE_SECONDS = 2


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
    # The MAC arrives as a call argument under the frozen --mode dispatch
    # (main.py extracts it from argv and passes it in). Run standalone from
    # source (python claim_connect_worker.py AA:BB:...), it comes from argv.
    if target_mac is None and len(sys.argv) >= 2:
        target_mac = sys.argv[1]
    if not target_mac:
        write_result("not_found", error="No MAC address given")
        return

    print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Worker started, looking for {target_mac}...", flush=True)
    write_result("connecting")

    last_error = "Could not connect to the droid"
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        droid = None
        try:
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Connect attempt {attempt}/{CONNECT_ATTEMPTS}...", flush=True)
            droid = await find_and_connect(target_mac)
            if droid is None:
                last_error = f"{target_mac} not seen during rescan"
                print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] {last_error}", flush=True)
            else:
                # silent=True -- the library's own "paired!" chirp+light cue
                # would clash with Disney's own activation show coming up next
                # on the Activation page. wait_for bounds the connect so a
                # stalled service-discovery can't hang forever.
                await asyncio.wait_for(droid.connect(silent=True), timeout=CONNECT_TIMEOUT_SECONDS)
                print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Connected successfully.", flush=True)
                update_env_values({"DROID_MAC": target_mac})
                write_result("found")
                try:
                    await droid.disconnect()
                except Exception:
                    pass
                return
        except asyncio.TimeoutError:
            last_error = "Connection timed out"
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Attempt {attempt} timed out after {CONNECT_TIMEOUT_SECONDS}s", flush=True)
        except asyncio.CancelledError:
            last_error = "Connection was cancelled"
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Attempt {attempt} cancelled", flush=True)
        except Exception as e:
            last_error = str(e)
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] Attempt {attempt} error:", flush=True)
            traceback.print_exc()

        # tidy up a half-open link before the next try
        if droid is not None:
            try:
                await droid.disconnect()
            except Exception:
                pass
        if attempt < CONNECT_ATTEMPTS:
            await asyncio.sleep(RETRY_PAUSE_SECONDS)

    print(f"[{time.strftime('%H:%M:%S')}] [CLAIM_CONNECT] All {CONNECT_ATTEMPTS} attempts failed: {last_error}", flush=True)
    write_result("not_found", error=last_error)


if __name__ == "__main__":
    asyncio.run(main())
