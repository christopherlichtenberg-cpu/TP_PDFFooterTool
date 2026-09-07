#!/usr/bin/env python3
"""
AffStamp - windowed front end.

Every button runs exactly the same code as the command line; the console
output each step produces is streamed into the log pane rather than thrown
away, because the warnings it prints are the point of the tool.

Long steps run on a worker thread so the window never freezes. Only the
main thread touches widgets - the worker posts text through a queue.
"""
from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# In a windowed (--noconsole) build there is no real stdout. Give the
# interpreter something harmless to write to before anything prints.
if sys.stdout is None or not hasattr(sys.stdout, "write"):
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None or not hasattr(sys.stderr, "write"):
    sys.stderr = open(os.devnull, "w")

import affstamp as A

PAD = 8


class _QueueWriter:
    """File-like object that funnels a worker's output to the UI thread."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


class AffStampGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AffStamp %s" % A.VERSION)
        self.minsize(940, 700)
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

        self.q = queue.Queue()
        self.busy = False
        self.buttons = []
        self.last_output = None

        st = A.load_state()
        self.base = tk.StringVar(value=st.get("base", ""))
        self.scan = tk.StringVar(value=st.get("scan", ""))
        self.out_dir = tk.StringVar(value=st.get("out_dir", ""))
        self.height = tk.StringVar(value=str(st.get("height", "")))
        self.edge = tk.StringVar(value=str(st.get("edge", "")))
        self.dx = tk.StringVar(value=str(st.get("dx", "0")))
        self.dy = tk.StringVar(value=str(st.get("dy", "0")))
        self.trial_pages = tk.StringVar(value="1-3")
        self.replace_last = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Ready.")

        self._build()
        self._show_tails()
        self._poll()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- layout ----------------------------------------------------------
    def _build(self):
        root = ttk.Frame(self, padding=PAD)
        root.pack(fill="both", expand=True)

        # ---- files
        f = ttk.LabelFrame(root, text="Files", padding=PAD)
        f.pack(fill="x")
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Hyperlinked PDF").grid(row=0, column=0, sticky="w")
        self.e_base = ttk.Entry(f, textvariable=self.base)
        self.e_base.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Browse...", width=11,
                   command=self._pick_base).grid(row=0, column=2)

        ttk.Label(f, text="Scan PDF").grid(row=1, column=0, sticky="w",
                                           pady=(6, 0))
        self.e_scan = ttk.Entry(f, textvariable=self.scan)
        self.e_scan.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(f, text="Browse...", width=11,
                   command=self._pick_scan).grid(row=1, column=2, pady=(6, 0))

        ttk.Label(f, text="Output folder").grid(row=2, column=0, sticky="w",
                                                pady=(6, 0))
        self.e_out = ttk.Entry(f, textvariable=self.out_dir)
        self.e_out.grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(f, text="Change...", width=11,
                   command=self._pick_outdir).grid(row=2, column=2,
                                                   pady=(6, 0))
        ttk.Label(f, text="Everything the tool writes goes here. It defaults "
                          "to the folder holding the hyperlinked PDF.",
                  foreground="#555").grid(row=3, column=1, sticky="w",
                                          padx=6, pady=(4, 0))

        # ---- settings
        s = ttk.LabelFrame(root, text="Settings", padding=PAD)
        s.pack(fill="x", pady=(PAD, 0))
        ttk.Label(s, text="Strip height").grid(row=0, column=0, sticky="w")
        ttk.Entry(s, textvariable=self.height, width=7).grid(row=0, column=1)
        ttk.Label(s, text="mm").grid(row=0, column=2, sticky="w", padx=(2, 16))

        ttk.Label(s, text="Edge trim").grid(row=0, column=3, sticky="w")
        ttk.Entry(s, textvariable=self.edge, width=7).grid(row=0, column=4)
        ttk.Label(s, text="mm").grid(row=0, column=5, sticky="w", padx=(2, 16))

        ttk.Label(s, text="Nudge  dx").grid(row=0, column=6, sticky="w")
        ttk.Entry(s, textvariable=self.dx, width=7).grid(row=0, column=7)
        ttk.Label(s, text="mm (+right)").grid(row=0, column=8, sticky="w",
                                              padx=(2, 12))
        ttk.Label(s, text="dy").grid(row=0, column=9, sticky="w")
        ttk.Entry(s, textvariable=self.dy, width=7).grid(row=0, column=10)
        ttk.Label(s, text="mm (+down)").grid(row=0, column=11, sticky="w",
                                             padx=(2, 0))
        ttk.Label(s, text="Run Measure to fill the height and edge trim in "
                          "automatically.", foreground="#555"
                  ).grid(row=1, column=0, columnspan=12, sticky="w",
                         pady=(6, 0))

        # ---- steps
        g = ttk.LabelFrame(root, text="Steps", padding=PAD)
        g.pack(fill="x", pady=(PAD, 0))
        for c in range(4):
            g.columnconfigure(c, weight=1)

        self._step(g, 0, 0, "1.  Check / repair links", self.do_links)
        self._step(g, 0, 1, "2.  Ghost overlay", self.do_compare)
        self._step(g, 0, 2, "3.  Measure", self.do_measure)
        self._step(g, 0, 3, "Ruler PDF", self.do_ruler)

        row = ttk.Frame(g)
        row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 4))
        ttk.Label(row, text="Trial pages").pack(side="left")
        ttk.Entry(row, textvariable=self.trial_pages,
                  width=12).pack(side="left", padx=(6, 16))
        ttk.Checkbutton(row, variable=self.replace_last,
                        text="Replace the final page with the scan's "
                             "(full run only)").pack(side="left")

        self._step(g, 2, 0, "4.  Trial stamp", self.do_trial)
        b = self._step(g, 2, 1, "5.  FULL STAMP", self.do_full)
        b.configure(style="Accent.TButton")
        self._step(g, 2, 2, "Audit  (optional)", self.do_audit)
        self._step(g, 2, 3, "Self-test", self.do_selftest)

        # ---- log
        lf = ttk.LabelFrame(root, text="Output", padding=(PAD, PAD, PAD, 4))
        lf.pack(fill="both", expand=True, pady=(PAD, 0))
        self.log = tk.Text(lf, wrap="none", height=18, font=("Consolas", 9),
                           background="#1b1b1b", foreground="#dcdcdc",
                           insertbackground="#dcdcdc", borderwidth=0)
        ys = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        xs = ttk.Scrollbar(lf, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

        self.log.tag_configure("warn", foreground="#ffc663")
        self.log.tag_configure("bad", foreground="#ff7b72")
        self.log.tag_configure("good", foreground="#7ee787")
        self.log.tag_configure("head", foreground="#79c0ff")
        self.log.configure(state="disabled")

        # ---- footer
        ft = ttk.Frame(root)
        ft.pack(fill="x", pady=(PAD, 0))
        ttk.Button(ft, text="Open output folder",
                   command=self.open_folder).pack(side="left")
        self.open_btn = ttk.Button(ft, text="Open last file",
                                   command=self.open_last, state="disabled")
        self.open_btn.pack(side="left", padx=6)
        ttk.Button(ft, text="Save log...",
                   command=self.save_log).pack(side="left")
        ttk.Button(ft, text="Clear", command=self.clear_log).pack(side="left",
                                                                  padx=6)
        self.bar = ttk.Progressbar(ft, mode="indeterminate", length=150)
        self.bar.pack(side="right")
        ttk.Label(ft, textvariable=self.status).pack(side="right", padx=10)

    def _step(self, parent, r, c, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
        b.grid(row=r, column=c, sticky="ew", padx=3, pady=3, ipady=4)
        self.buttons.append(b)
        return b

    # -- file pickers ----------------------------------------------------
    def _pick_base(self):
        p = filedialog.askopenfilename(
            title="Select the hyperlinked PDF (the Word export)",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if p:
            self.base.set(p)
            self._show_tails()
            if not self.out_dir.get() or not os.path.isdir(self.out_dir.get()):
                self.out_dir.set(os.path.dirname(os.path.abspath(p)))
            else:
                self.out_dir.set(os.path.dirname(os.path.abspath(p)))
            self._save()

    def _pick_scan(self):
        p = filedialog.askopenfilename(
            title="Select the scanned signed PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if p:
            self.scan.set(p)
            self._show_tails()
            self._save()

    def _pick_outdir(self):
        d = filedialog.askdirectory(title="Where should output files go?",
                                    initialdir=self.out_dir.get() or ".")
        if d:
            self.out_dir.set(d)
            self._show_tails()
            self._save()

    def _show_tails(self):
        """A long path is only useful if you can see the filename end."""
        for e in (self.e_base, self.e_scan, self.e_out):
            e.after_idle(lambda w=e: w.xview_moveto(1.0))

    def _save(self):
        st = {"base": self.base.get(), "scan": self.scan.get(),
              "out_dir": self.out_dir.get()}
        for k, v in (("height", self.height), ("edge", self.edge),
                     ("dx", self.dx), ("dy", self.dy)):
            if v.get().strip():
                st[k] = v.get().strip()
        A.save_state(st)

    def _close(self):
        self._save()
        self.destroy()

    # -- logging ---------------------------------------------------------
    def _append(self, text, tag=None):
        self.log.configure(state="normal")
        for line in text.splitlines(keepends=True):
            t = tag
            if t is None:
                bare = line.strip()
                if bare.startswith("!!"):
                    t = "warn"
                elif ("ERROR" in bare or "FAILED" in bare
                        or "MISMATCH" in bare or "DO NOT USE" in bare):
                    t = "bad"
                elif bare.startswith("OK") or "PASSED" in bare or "Good." in bare:
                    t = "good"
            self.log.insert("end", line, t or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def save_log(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".txt", initialdir=self.out_dir.get() or ".",
            initialfile="affstamp_log.txt",
            filetypes=[("Text files", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.log.get("1.0", "end"))
            self.status.set("Log saved.")

    # -- running ---------------------------------------------------------
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple):
                    self._finish(*item)
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.after(60, self._poll)

    def run(self, args, label, done=None, output=None):
        if self.busy:
            return
        self.busy = True
        for b in self.buttons:
            b.configure(state="disabled")
        self.bar.start(12)
        self.status.set(label + "...")
        self._append("\n" + "=" * 72 + "\n%s\n" % label + "=" * 72 + "\n",
                     "head")
        self.pending_output = output

        def work():
            rc, buf = 1, _QueueWriter(self.q)
            try:
                with contextlib.redirect_stdout(buf), \
                        contextlib.redirect_stderr(buf):
                    ns = A.build_parser().parse_args(args)
                    rc = ns.func(ns)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
                buf.write("\n%s\n" % e)
            except Exception:
                rc = 1
                buf.write("\n" + traceback.format_exc())
            self.q.put((rc, label, done))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, rc, label, done):
        self.busy = False
        self.bar.stop()
        self.bar.configure(value=0)
        for b in self.buttons:
            b.configure(state="normal")
        self.status.set("%s: %s" % (label,
                                    "done" if rc == 0 else "finished (%s)" % rc))
        out = getattr(self, "pending_output", None)
        if out and os.path.isfile(out):
            self.last_output = out
            self.open_btn.configure(state="normal")
        if done:
            try:
                done(rc)
            except Exception:
                self._append(traceback.format_exc(), "bad")

    # -- validation ------------------------------------------------------
    def _files(self, need_scan=True):
        b, s = self.base.get().strip(), self.scan.get().strip()
        if not b or not os.path.isfile(b):
            messagebox.showerror("AffStamp",
                                 "Choose the hyperlinked PDF first.")
            return None
        if need_scan and (not s or not os.path.isfile(s)):
            messagebox.showerror("AffStamp", "Choose the scan PDF first.")
            return None
        if not self.out_dir.get().strip():
            self.out_dir.set(os.path.dirname(os.path.abspath(b)))
        self._save()
        return b, s

    def _number(self, var, name, required=True):
        v = var.get().strip()
        if not v:
            if required:
                messagebox.showerror("AffStamp", "Set the %s first." % name)
                return None
            return None
        try:
            return float(v)
        except ValueError:
            messagebox.showerror("AffStamp",
                                 "%s must be a number, not %r." % (name, v))
            return None

    def _out(self, filename):
        return os.path.join(self.out_dir.get() or ".", filename)

    # -- steps -----------------------------------------------------------
    def do_links(self):
        f = self._files(need_scan=False)
        if not f:
            return
        base, _ = f

        def after(rc):
            self._append("\n")
            if messagebox.askyesno(
                    "Repair links",
                    "Rewrite any absolute paths as relative ones?\n\n"
                    "Only needed if the output above flagged links as ABS.\n"
                    "A repaired copy is written and AffStamp switches to it."):
                out = self._out(os.path.splitext(os.path.basename(base))[0]
                                + "_fixed.pdf")

                def switch(rc2):
                    if rc2 == 0 and os.path.isfile(out):
                        self.base.set(out)
                        self._save()
                        self._append("\nNow using %s as the hyperlinked PDF.\n"
                                     % os.path.basename(out), "good")
                self.run(["links", "--base", base, "--fix-relative",
                          "--out", out, "--check"],
                         "Repair links", done=switch, output=out)

        self.run(["links", "--base", base, "--dump", "--check"],
                 "Check links", done=after)

    def do_compare(self):
        f = self._files()
        if not f:
            return
        out = self._out("GHOST.pdf")

        def after(rc):
            if rc == 0:
                messagebox.showinfo(
                    "Ghost overlay",
                    "Wrote GHOST.pdf.\n\nOpen it and flip through every "
                    "page.\n\nBlack = hyperlinked PDF, red = scan.\n\n"
                    "Red sitting on black means the pagination matches. Two "
                    "different pages superimposed means a page break has "
                    "moved - stop and fix the Word document.")
        self.run(["compare", "--base", f[0], "--scan", f[1], "--out", out],
                 "Ghost overlay", done=after, output=out)

    def do_measure(self):
        f = self._files()
        if not f:
            return
        A.LAST_SUGGESTED.clear()

        def after(rc):
            h = A.LAST_SUGGESTED.get("height")
            if h:
                self.height.set(str(h))
                if A.LAST_SUGGESTED.get("edge"):
                    self.edge.set(str(A.LAST_SUGGESTED["edge"]))
                self._save()
                self._append("\nFilled in: strip height %s mm%s\n"
                             % (h, ", edge trim %s mm" % self.edge.get()
                                if A.LAST_SUGGESTED.get("edge") else ""),
                             "good")
        self.run(["measure", "--base", f[0], "--scan", f[1]],
                 "Measure", done=after)

    def do_ruler(self):
        f = self._files()
        if not f:
            return
        args = ["ruler", "--scan", f[1], "--out-dir", self.out_dir.get()]
        h = self._number(self.height, "strip height", required=False)
        if h:
            args += ["--height", str(h)]
        if self.trial_pages.get().strip():
            args += ["--pages", self.trial_pages.get().strip()]
        out = self._out(os.path.splitext(os.path.basename(f[1]))[0]
                        + "_RULER.pdf")
        self.run(args, "Ruler PDF", output=out)

    def _stamp_args(self, base, scan):
        h = self._number(self.height, "strip height")
        if h is None:
            return None
        args = ["stamp", "--base", base, "--scan", scan, "--height", str(h)]
        e = self._number(self.edge, "edge trim", required=False)
        if e:
            args += ["--edge", str(e)]
        for var, flag in ((self.dx, "--dx"), (self.dy, "--dy")):
            v = self._number(var, flag, required=False)
            if v:
                args += [flag, str(v)]
        return args

    def do_trial(self):
        f = self._files()
        if not f:
            return
        args = self._stamp_args(*f)
        if args is None:
            return
        out = self._out("TEST.pdf")
        args += ["--out", out, "--skip-last"]
        if self.trial_pages.get().strip():
            args += ["--pages", self.trial_pages.get().strip()]

        def after(rc):
            if rc == 0:
                messagebox.showinfo(
                    "Trial run",
                    "Wrote TEST.pdf.\n\nPrint a page and hold it against the "
                    "original.\n\nIf the marks sit slightly off, set the "
                    "nudge (dx positive = right, dy positive = down) and run "
                    "the trial again.")
        self.run(args, "Trial stamp", done=after, output=out)

    def _last_page_links(self, base):
        try:
            d = A.pymupdf.open(base)
            n = [A.link_target(l) for l in d[d.page_count - 1].get_links()]
            d.close()
            return n
        except Exception:
            return []

    def do_full(self):
        f = self._files()
        if not f:
            return
        base, scan = f
        args = self._stamp_args(base, scan)
        if args is None:
            return

        repl = self.replace_last.get()
        msg = ["Stamp every page of:\n  %s\n" % os.path.basename(base)]
        if repl:
            msg.append("The final page will be REPLACED wholesale with the "
                       "scan's final page.")
            lost = self._last_page_links(base)
            if lost:
                msg.append("\nWarning: the final page carries %d link(s), "
                           "which will be destroyed with it:\n  %s\n\nYou "
                           "would need to re-add them in Acrobat."
                           % (len(lost), "\n  ".join(lost[:6])))
            else:
                msg.append("It carries no links, so nothing is lost.")
        else:
            msg.append("The final page will be left untouched for you to "
                       "replace in Acrobat.")
        msg.append("\nProceed?")
        if not messagebox.askyesno("Full run", "\n".join(msg)):
            return

        args += ["--replace-last"] if repl else ["--skip-last"]
        out = self._out(os.path.splitext(os.path.basename(base))[0]
                        + "_SIGNED.pdf")
        args += ["--out", out]

        def after(rc):
            if rc == 0:
                messagebox.showinfo(
                    "Full run complete",
                    "Wrote %s\n\nAll link annotations checked and preserved."
                    "\n\nNext: check it in Acrobat, then certify.\n\nNever "
                    "run Save As Other > Optimized PDF or Reduce File Size "
                    "on it." % os.path.basename(out))
            elif rc == 4:
                messagebox.showwarning(
                    "Pages with no signature ink",
                    "The file was written, but some pages had no signature "
                    "ink in the strip.\n\nSee the log. Check those pages in "
                    "the scan before relying on the file.")
            else:
                messagebox.showerror(
                    "Problem",
                    "The run reported a problem (code %s). Read the log "
                    "before using the output." % rc)
        self.run(args, "FULL STAMP", done=after, output=out)

    def do_audit(self):
        f = self._files()
        if not f:
            return
        band = self._number(self.height, "strip height", required=False) or 30
        self.run(["audit", "--base", f[0], "--scan", f[1],
                  "--band", str(band)], "Audit")

    def do_selftest(self):
        self.run(["selftest"], "Self-test")

    # -- opening things --------------------------------------------------
    def open_folder(self):
        d = self.out_dir.get() or "."
        if os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showerror("AffStamp", "No output folder set yet.")

    def open_last(self):
        if self.last_output and os.path.isfile(self.last_output):
            try:
                os.startfile(self.last_output)
            except OSError as e:
                messagebox.showerror("AffStamp", str(e))


def main():
    app = AffStampGUI()
    app._append(
        "AffStamp %s\n\n"
        "1. Choose the hyperlinked PDF and the scan above.\n"
        "2. Work down the numbered steps left to right.\n"
        "3. Everything written goes to the output folder.\n\n"
        "The files and settings are remembered next time you open this.\n"
        % A.VERSION, "head")
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
