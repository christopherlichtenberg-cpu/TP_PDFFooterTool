#!/usr/bin/env python3
"""
Drives the AffStamp window headlessly.

Tk needs a display, and the machines this ships to have no test harness, so
`tests/_stub_tk` provides a stand-in tkinter that records widgets and lets the
callbacks be called directly.  It is enough to prove the wiring: threading,
log capture, the fields Measure fills in, the guard rails, the confirmation
dialog, and state surviving a restart.  It says nothing about how the window
looks - open it for that.

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
SCAN = os.path.join(WORK, "signed_scan.pdf")


def run_and_wait(win, fn, label, timeout=300):
    fn()
    started = time.time()
    while win.busy and time.time() - started < timeout:
        tkinter.pump(50)
        time.sleep(0.05)
    tkinter.pump(300)
    print("       [%s] status=%r" % (label, win.v_status.get()))


def main():
    make_base(BASE, links="abs")
    make_scan(SCAN)

    root = tkinter.Tk()
    win = affstamp_gui.AffStampWindow(root)

    section("construction")
    check("all step buttons created", len(win.buttons) == 8, len(win.buttons))
    check("writer routed into the window", affstamp._WRITER == win._writer)
    check("prompts routed into dialogs", affstamp._ASKER == win._asker)

    section("guard rails")
    messagebox.ANSWER = True
    before = len(tkinter.DIALOGS)
    win.do_measure()
    check("refuses to run before the files are chosen",
          len(tkinter.DIALOGS) > before and tkinter.DIALOGS[-1][0] == "error")
    check("stayed idle", not win.busy)

    win.v_base.set(BASE)
    win.v_scan.set(SCAN)
    win.v_out.set(WORK)

    win.v_height.set("twenty")
    before = len(tkinter.DIALOGS)
    win.do_trial()
    check("non-numeric height refused",
          len(tkinter.DIALOGS) > before and tkinter.DIALOGS[-1][0] == "error")
    win.v_height.set("19")
    win.v_edge.set("99")
    before = len(tkinter.DIALOGS)
    win.do_trial()
    check("edge trim >= strip height refused",
          len(tkinter.DIALOGS) > before and tkinter.DIALOGS[-1][0] == "error")
    win.v_edge.set("0")

    section("links")
    run_and_wait(win, win.do_links, "links")
    check("switched to the repaired file",
          win.v_base.get().endswith("_fixed.pdf"), win.v_base.get())

    section("measure fills the settings in")
    run_and_wait(win, win.do_measure, "measure")
    check("strip height auto-filled", float(win.v_height.get() or 0) > 0,
          win.v_height.get())
    check("edge trim auto-filled", float(win.v_edge.get() or 0) > 0,
          win.v_edge.get())
    check("output pane captured the run",
          any("SUGGESTED" in text for text, _ in win.text.lines))
    check("output pane is colour-coded",
          {level for _, level in win.text.lines} >= {"head", "ok"})

    section("trial stamp")
    win.v_trial.set("1-2")
    run_and_wait(win, win.do_trial, "trial")
    check("TEST.pdf written", os.path.isfile(os.path.join(WORK, "TEST.pdf")))
    check("Open last file has something to open",
          (win.session.last_output or "").endswith("TEST.pdf"))
    check("buttons re-enabled afterwards",
          all("disabled" not in b.state() for b in win.buttons))

    section("full run")
    win.v_replace.set(True)
    before = len(tkinter.DIALOGS)
    run_and_wait(win, win.do_full, "full")
    confirmations = [m for kind, m in tkinter.DIALOGS[before:]
                     if kind == "askyesno"]
    check("confirmation shown first", bool(confirmations))
    check("confirmation names the links the swap would destroy",
          bool(confirmations) and "WARNING" in confirmations[0]
          and "hyperlink" in confirmations[0])
    signed = os.path.join(WORK, "hyperlinked_fixed_SIGNED.pdf")
    check("_SIGNED.pdf written", os.path.isfile(signed))

    os.remove(signed)
    messagebox.ANSWER = False
    win.do_full()
    tkinter.pump(300)
    check("declining the confirmation writes nothing",
          not os.path.isfile(signed))
    check("the refusal is logged",
          any("cancelled" in text.lower() for text, _ in win.text.lines))
    messagebox.ANSWER = True

    section("state survives a restart")
    win.on_close()
    tkinter.pump()
    again = affstamp_gui.AffStampWindow(tkinter.Tk())
    check("files restored", again.v_base.get() == win.v_base.get())
    check("height restored", float(again.v_height.get() or 0) > 0)
    check("trial pages restored", again.session.trial_pages == "1-2")

    section("self-test through the window")
    run_and_wait(again, again.do_selftest, "selftest")
    check("self-test passes in the GUI",
          any("SELFTEST PASSED" in text for text, _ in again.text.lines))

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
