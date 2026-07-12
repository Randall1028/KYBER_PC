"""
main.py -- single frozen entry point for KYBER PC.

Once packaged by PyInstaller there is no python.exe to hand a script to, so
every one of KYBER's processes -- the tray shell, the Mainframe config server,
the kyber_core brain, and the one-shot BLE/mapper workers -- is reached by
re-invoking the ONE frozen binary with a --mode flag. This file is that
switchboard.

Two things make it correct under a frozen build:

1. multiprocessing.freeze_support() runs FIRST. kyber_core spawns its BLE and
   Whisper children via multiprocessing; on Windows ("spawn") each child
   re-executes this exe. freeze_support() detects that re-exec, runs the
   child's target, and exits -- so a spawned child never falls through to the
   argument parsing below (which would otherwise re-launch the whole app).

2. Each mode imports its module lazily, inside its own branch. That preserves
   the exact process isolation the split-process design depends on: a
   `--mode claim` process imports bleak but never the server's sounddevice/
   numpy, and a `--mode mainframe` process imports neither bleak nor winrt.

From source this file is optional -- `python tray_shell.py` and the individual
worker scripts still run exactly as before. It exists so the frozen exe has a
single, predictable entry point.

    KYBER.exe                                    # defaults to the tray shell
    KYBER.exe --mode mainframe
    KYBER.exe --mode core
    KYBER.exe --mode claim
    KYBER.exe --mode claim_connect AA:BB:CC:DD:EE:FF
    KYBER.exe --mode sound_mapper
"""

import multiprocessing
import os
import sys


def _dispatch():
    import argparse

    parser = argparse.ArgumentParser(prog="KYBER", add_help=False)
    parser.add_argument("--mode", default="tray")
    parser.add_argument("extra", nargs="*")
    args, _unknown = parser.parse_known_args()

    mode = args.mode

    if mode == "tray":
        import tray_shell
        tray_shell.main()
    elif mode == "mainframe":
        import kyber_config_server
        kyber_config_server.main()
    elif mode == "core":
        import kyber_core
        kyber_core.main()
    elif mode == "claim":
        import asyncio
        import claim_worker
        asyncio.run(claim_worker.main())
    elif mode == "claim_connect":
        import asyncio
        import claim_connect_worker
        target_mac = args.extra[0] if args.extra else None
        asyncio.run(claim_connect_worker.main(target_mac))
    elif mode == "sound_mapper":
        import sound_discovery_server
        sound_discovery_server.main()
    else:
        raise SystemExit("[KYBER] Unknown --mode: %r" % (mode,))


def _ensure_std_streams():
    """A windowed PyInstaller build (console=False) sets sys.stdout/stderr to
    None, so ANYTHING that writes to them -- tqdm inside huggingface_hub, and
    every print() in the workers and the brain -- dies with
    "'NoneType' object has no attribute 'write'". Point any missing stream at a
    sink so every mode and spawned child is safe. Runs before freeze_support so
    multiprocessing children inherit the fix too."""
    import io
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w"))
            except Exception:
                setattr(sys, name, io.StringIO())


if __name__ == "__main__":
    _ensure_std_streams()
    # freeze_support MUST be right after -- see module docstring, point 1.
    multiprocessing.freeze_support()
    _dispatch()
