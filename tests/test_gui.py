#!/usr/bin/env python3
"""
Drives the AffStamp window headlessly.

Tk needs a display, and the machines this ships to have no test harness, so
`tests/_stub_tk` provides a stand-in tkinter that records widgets and lets
the callbacks be called directly. It proves the wiring - construction, the
step callbacks, log capture, saved state - not how the window looks. Open it
with run.bat for that.

    py tests/test_gui.py
"""

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_stub_tk"))      # fake tkinter first
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

WORK = tempfile.mkdtemp(prefix="affstamp_gui_tests_")
os.environ["XDG_DATA_HOME"] = os.path.join(WORK, "state")
os.environ["LOCALAPPDATA"] = os.path.join(WORK, "state")

import tkinter
import tkinter.messagebox as messagebox
from test_affstamp import make_base, make_scan, check, FAILURES, section

import affstamp
import affstamp_gui

BASE = os.path.join(WORK, "hyperlinked.pdf")
SCAN = os.path.join(WORK, "scan.pdf")


def settle(win, timeout=300):
    started = time.time()
    while time.time() - started < timeout:
        tkinter.pump(60)
        busy = getattr(win, "busy", False)
        if callable(busy):
            busy = busy()
        if not busy:
            break
        time.sleep(0.05)
    tkinter.pump(400)


def main():
    make_base(BASE, absolute=False)
    make_scan(SCAN)

    section("construction")
    win = affstamp_gui.AffStampGUI()
    check("window built", win is not None)
    check("step buttons created", len(getattr(win, "buttons", [])) >= 6,
          len(getattr(win, "buttons", [])))
    for name in ("do_links", "do_compare", "do_measure", "do_ruler",
                 "do_trial", "do_full", "do_audit", "do_selftest"):
        check("has %s" % name, callable(getattr(win, name, None)))

    section("fields")
    for name in ("base", "scan", "out_dir", "height", "edge", "dx", "dy",
                 "trial_pages", "replace_last"):
        check("variable %s is wired" % name,
              hasattr(win, name) and hasattr(getattr(win, name), "get"))

    win.base.set(BASE)
    win.scan.set(SCAN)
    win.out_dir.set(WORK)
    messagebox.ANSWER = True

    section("self-test through the window")
    win.do_selftest()
    settle(win)
    logged = "".join(t for t, _ in win.log.lines) if hasattr(win, "log") else ""
    check("the window captured the run output", bool(logged.strip()),
          repr(logged[:120]))
    check("self-test passed in the GUI", "SELFTEST PASSED" in logged,
          repr(logged[-200:]))

    section("measure through the window")
    win.do_measure()
    settle(win)
    logged = "".join(t for t, _ in win.log.lines)
    check("measure ran in the GUI", "SUGGESTED" in logged or "height" in logged,
          repr(logged[-200:]))

    print("\n%s" % ("=" * 60))
    if FAILURES:
        print("%d FAILURE(S):" % len(FAILURES))
        for name in FAILURES:
            print("   %s" % name)
        return 1
    print("all GUI tests passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(code)
