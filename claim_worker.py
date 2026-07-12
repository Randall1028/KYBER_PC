"""
claim_worker.py -- scans for nearby droids and reports every one it finds,
as a genuinely separate process from kyber_config_server.py.

Why a separate SCRIPT, not just multiprocessing.Process from within the
Mainframe server: on Windows, multiprocessing's "spawn" method re-imports
the entire parent module in the child process before calling the target
function -- meaning if this worker were defined inside kyber_config_server.py,
the spawned process would still import that file's top-level sounddevice/
numpy (needed there for Mic Check), reproducing the exact "Thread configured
for Windows GUI" conflict kyber_core.py's two-process split was built to
avoid. A truly separate script, launched via subprocess.Popen, never
imports kyber_config_server.py at all -- so there's nothing for bleak/winrt
to conflict with in this process.

Scope: SCAN ONLY -- lists every distinct droid seen during the full
discovery window, does NOT connect to any of them. Matches Pi's real
droid_scan() (scans the whole window, collects every match into a dict
keyed by address, never stops early) -- this used to stop and connect on
the first match, which is exactly why the picker never showed more than
one result: there was never anything to pick from. Connecting to a
specific chosen droid is claim_connect_worker.py's job now, triggered
separately once the user picks one from the list.

Writes its result to claim_result.json in the same folder, which
kyber_config_server.py polls from its /setup/claim_status endpoint.

Run directly (normally launched by the Mainframe server, not by hand):
    python claim_worker.py
"""

import asyncio
import json
import os
import time
import traceback

from bleak import BleakScanner
from droiddepot.protocol import DisneyBLEManufacturerId

from config import PROJECT_DIR

RESULT_PATH = os.path.join(PROJECT_DIR, "claim_result.json")
DISCOVERY_TIMEOUT_SECONDS = 15


def write_result(status: str, droids=None, error: str = ""):
    with open(RESULT_PATH, "w") as f:
        json.dump({"status": status, "droids": droids or [], "error": error}, f)


async def scan_for_droids():
    """Scans the FULL discovery window rather than returning on the first
    match -- same approach Pi's real droid_scan() uses (found = {}, keyed
    by address, never breaks early) -- so multiple droids in range all get
    listed instead of only ever seeing whichever one happened to advertise
    first."""
    found = {}
    async with BleakScanner() as scanner:
        deadline = time.time() + DISCOVERY_TIMEOUT_SECONDS
        while time.time() < deadline:
            devices = scanner.discovered_devices_and_advertisement_data
            for addr, (ble_device, adv_data) in devices.items():
                mfr = adv_data.manufacturer_data or {}
                if DisneyBLEManufacturerId.DroidManufacturerId in mfr:
                    found[addr.upper()] = addr.upper()
            await asyncio.sleep(1)
    return list(found.keys())


async def main():
    print(f"[{time.strftime('%H:%M:%S')}] [CLAIM] Worker started, beginning scan...", flush=True)
    write_result("scanning")

    try:
        macs = await scan_for_droids()
        if not macs:
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM] Scan finished -- no droids found.", flush=True)
            write_result("not_found")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] [CLAIM] Scan finished -- found {len(macs)}: {macs}", flush=True)
            write_result("found", droids=[{"mac": m} for m in macs])

    except Exception as e:
        # Full traceback, not just str(e) -- so a totally unrelated failure
        # (import, permissions, anything) is visible here instead of being
        # silently flattened into a generic "not found" that looks
        # identical to a real empty scan.
        print("[CLAIM] Exception during scan:", flush=True)
        traceback.print_exc()
        write_result("not_found", error=str(e))


if __name__ == "__main__":
    asyncio.run(main())
