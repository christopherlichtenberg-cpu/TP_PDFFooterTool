#!/usr/bin/env python3
"""
AffStamp - the window.

A thin Tk shell over affstamp.py.  Every step is the same code the command
line runs; this only collects the settings, runs the step on a worker thread
so the window stays responsive, and colours what it prints.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import affstamp
from affstamp import APP, VERSION

PAD = 8
MONO = ("Consolas", 9) if os.name == "nt" else ("DejaVu Sans Mono", 9)

COLOURS = {
    "info":  "#111111",
    "head":  "#0b3d6b",
    "warn":  "#8a5300",
    "error": "#a11212",
    "ok":    "#12662b",
}


def open_in_explorer(path: str) -> None:
    """Show a file or folder in whatever the platform uses for that."""
    if not path or not os.path.exists(path):
        return
    try:
        if os.name == "nt":
            if os.path.isdir(path):
                os.startfile(path)                      # noqa: S606
            else:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            if os.path.isdir(path):
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open",
                              path if os.path.isdir(path)
                              else os.path.dirname(path)])
    except Exception:
        pass


def open_file(path: str) -> None:
    if not path or not os.path.isfile(path):
        return
    try:
        if os.name == "nt":
            os.startfile(path)                          # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class AffStampWindow(object):

    def __init__(self, root: tk.Tk):
        self.root = root
        self.session = affstamp.Session()
        self.queue = queue.Queue()
        self.busy = False

        root.title("%s %s" % (APP, VERSION))
        root.minsize(940, 680)
        try:
            root.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass

        style = ttk.Style()
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                    break
                except Exception:
                    continue
        style.configure("Run.TButton", font=("Segoe UI", 9, "bold"))

        self.v_base = tk.StringVar(value=self.session.base)
        self.v_scan = tk.StringVar(value=self.session.scan)
        self.v_out = tk.StringVar(value=self.session.out_dir)
        self.v_height = tk.StringVar(value=self._fmt(self.session.height))
        self.v_edge = tk.StringVar(value=self._fmt(self.session.edge))
        self.v_dx = tk.StringVar(value=self._fmt(self.session.dx))
        self.v_dy = tk.StringVar(value=self._fmt(self.session.dy))
        self.v_trial = tk.StringVar(value=self.session.trial_pages)
        self.v_replace = tk.BooleanVar(value=self.session.replace_last)
        self.v_status = tk.StringVar(value="Ready.")

        self._build_files(root)
        self._build_settings(root)
        self._build_steps(root)
        self._build_output(root)
        self._build_footer(root)

        affstamp.set_writer(self._writer)
        affstamp.set_asker(self._asker)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._drain)

        self.log("%s %s" % (APP, VERSION), "head")
        self.log("Choose the two PDFs, then work left to right through the "
                 "numbered buttons.")
        self.log("First time on this machine, click Self-test.")
        if self.session.base or self.session.scan:
            self.log("Restored the files and settings from last time.", "ok")

    # -- fields ------------------------------------------------------------

    @staticmethod
    def _fmt(value) -> str:
        try:
            f = float(value or 0)
        except (TypeError, ValueError):
            return "0"
        return ("%g" % f) if f else "0"

    def _build_files(self, root):
        box = ttk.LabelFrame(root, text="Files", padding=PAD)
        box.pack(fill="x", padx=PAD, pady=(PAD, 4))
        box.columnconfigure(1, weight=1)

        rows = (("Hyperlinked PDF", self.v_base, self.pick_base, "Browse..."),
                ("Scan PDF", self.v_scan, self.pick_scan, "Browse..."),
                ("Output folder", self.v_out, self.pick_out, "Change..."))
        self.entries = []
        for r, (label, var, cmd, button) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w",
                                            padx=(0, PAD), pady=2)
            entry = ttk.Entry(box, textvariable=var)
            entry.grid(row=r, column=1, sticky="ew", pady=2)
            ttk.Button(box, text=button, command=cmd, width=11).grid(
                row=r, column=2, padx=(PAD, 0), pady=2)
            self.entries.append(entry)
        self._show_tails()

    def _show_tails(self):
        # A long path is far more useful read from the file name end.
        for entry in getattr(self, "entries", []):
            try:
                entry.xview_moveto(1.0)
            except Exception:
                pass

    def _build_settings(self, root):
        box = ttk.LabelFrame(root, text="Settings", padding=PAD)
        box.pack(fill="x", padx=PAD, pady=4)

        def field(parent, label, var, suffix, col):
            ttk.Label(parent, text=label).grid(row=0, column=col, sticky="e",
                                               padx=(0 if col == 0 else PAD*2, 4))
            ttk.Entry(parent, textvariable=var, width=7).grid(
                row=0, column=col + 1, sticky="w")
            ttk.Label(parent, text=suffix).grid(row=0, column=col + 2,
                                                sticky="w", padx=(4, 0))

        field(box, "Strip height", self.v_height, "mm above the page edge", 0)
        field(box, "Edge trim", self.v_edge, "mm", 3)
        field(box, "Nudge dx", self.v_dx, "mm (+ right)", 6)
        field(box, "dy", self.v_dy, "mm (+ down)", 9)

    def _build_steps(self, root):
        box = ttk.LabelFrame(root, text="Steps", padding=PAD)
        box.pack(fill="x", padx=PAD, pady=4)

        top = ttk.Frame(box)
        top.pack(fill="x")
        self.buttons = []

        def button(parent, text, cmd, style=None, width=22):
            b = ttk.Button(parent, text=text, command=cmd, width=width)
            if style:
                b.configure(style=style)
            b.pack(side="left", padx=(0, PAD))
            self.buttons.append(b)
            return b

        button(top, "1. Check / repair links", self.do_links)
        button(top, "2. Ghost overlay", self.do_ghost, width=18)
        button(top, "3. Measure", self.do_measure, width=14)
        button(top, "Ruler PDF", self.do_ruler, width=13)

        mid = ttk.Frame(box)
        mid.pack(fill="x", pady=(PAD, 0))
        ttk.Label(mid, text="Trial pages").pack(side="left")
        ttk.Entry(mid, textvariable=self.v_trial, width=10).pack(
            side="left", padx=(4, PAD * 2))
        ttk.Checkbutton(mid, text="Replace the final page with the scan's",
                        variable=self.v_replace).pack(side="left")

        low = ttk.Frame(box)
        low.pack(fill="x", pady=(PAD, 0))
        button(low, "4. Trial stamp", self.do_trial, width=18)
        button(low, "5. FULL STAMP", self.do_full, style="Run.TButton",
               width=18)
        button(low, "Audit", self.do_audit, width=13)
        button(low, "Self-test", self.do_selftest, width=13)

    def _build_output(self, root):
        box = ttk.LabelFrame(root, text="Output", padding=4)
        box.pack(fill="both", expand=True, padx=PAD, pady=4)
        self.text = tk.Text(box, wrap="word", font=MONO, height=18,
                            background="#fbfbfb", relief="flat",
                            borderwidth=0, state="disabled")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=bar.set)
        self.text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        for level, colour in COLOURS.items():
            self.text.tag_configure(level, foreground=colour)
        self.text.tag_configure("head", foreground=COLOURS["head"],
                                font=(MONO[0], MONO[1], "bold"))
        self.text.tag_configure("error", foreground=COLOURS["error"],
                                font=(MONO[0], MONO[1], "bold"))

    def _build_footer(self, root):
        box = ttk.Frame(root, padding=(PAD, 0, PAD, PAD))
        box.pack(fill="x")
        ttk.Button(box, text="Open output folder", width=18,
                   command=self.open_folder).pack(side="left")
        ttk.Button(box, text="Open last file", width=15,
                   command=self.open_last).pack(side="left", padx=PAD)
        ttk.Button(box, text="Save log...", width=12,
                   command=self.save_log).pack(side="left")
        ttk.Button(box, text="Clear", width=8,
                   command=self.clear_log).pack(side="left", padx=PAD)
        self.progress = ttk.Progressbar(box, mode="indeterminate", length=150)
        self.progress.pack(side="right")
        ttk.Label(box, textvariable=self.v_status).pack(side="right",
                                                        padx=PAD)

    # -- log ---------------------------------------------------------------

    def _writer(self, msg: str, level: str = "info") -> None:
        """Called from the worker thread - Tk is only touched in _drain."""
        self.queue.put((msg, level))

    def log(self, msg: str, level: str = "info") -> None:
        self.queue.put((msg, level))

    def _drain(self) -> None:
        try:
            while True:
                msg, level = self.queue.get_nowait()
                if msg == "__done__":
                    self._finish(*level)
                    continue
                self.text.configure(state="normal")
                self.text.insert("end", msg + "\n", level)
                self.text.see("end")
                self.text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _asker(self, question: str, default: bool) -> bool:
        """affstamp asks a yes/no question; show it as a dialog.

        Called from the worker thread, so the dialog is scheduled onto the
        main thread and the worker waits for the answer.
        """
        if threading.current_thread() is threading.main_thread():
            return bool(messagebox.askyesno(APP, question, parent=self.root))
        box, done = {}, threading.Event()

        def show():
            try:
                box["answer"] = bool(messagebox.askyesno(APP, question,
                                                         parent=self.root))
            except Exception:
                box["answer"] = default
            finally:
                done.set()

        self.root.after(0, show)
        done.wait()
        return box.get("answer", default)

    # -- running -----------------------------------------------------------

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.busy = busy
        for b in self.buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.v_status.set("%s..." % label)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)   # or it leaves a block behind

    def _run(self, cmd: str, opts: dict, label: str, after=None) -> None:
        if self.busy:
            return
        self.log("")
        self._set_busy(True, label)

        def work():
            rc = 2
            try:
                rc = self.session.run(cmd, opts)
            except Exception:
                for line in traceback.format_exc().splitlines():
                    self._writer("   " + line, "error")
            finally:
                self.queue.put(("__done__", (rc, label, after)))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, rc: int, label: str, after) -> None:
        self._set_busy(False)
        self.v_status.set("%s: %s" % (label, "done" if rc == 0 else
                                      "finished with problems"))
        self._refresh_fields()
        if callable(after):
            try:
                after(rc)
            except Exception:
                pass

    def _refresh_fields(self) -> None:
        s = self.session
        self.v_base.set(s.base)
        self.v_scan.set(s.scan)
        self.v_out.set(s.out_dir)
        self.v_height.set(self._fmt(s.height))
        self.v_edge.set(self._fmt(s.edge))
        self.v_dx.set(self._fmt(s.dx))
        self.v_dy.set(self._fmt(s.dy))
        self._show_tails()

    # -- validation --------------------------------------------------------

    def _number(self, var, label: str, default=0.0):
        raw = (var.get() or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            messagebox.showerror(APP, "%s must be a number, not %r."
                                 % (label, raw), parent=self.root)
            return None

    def _collect(self, need_height: bool = False) -> bool:
        """Pull the fields into the session, complaining about anything odd."""
        base, scan = self.v_base.get().strip(), self.v_scan.get().strip()
        if not base or not os.path.isfile(base):
            messagebox.showerror(APP, "Choose the hyperlinked PDF first.",
                                 parent=self.root)
            return False
        if not scan or not os.path.isfile(scan):
            messagebox.showerror(APP, "Choose the scan PDF first.",
                                 parent=self.root)
            return False

        values = {}
        for key, var, label in (("height", self.v_height, "Strip height"),
                                ("edge", self.v_edge, "Edge trim"),
                                ("dx", self.v_dx, "Nudge dx"),
                                ("dy", self.v_dy, "Nudge dy")):
            value = self._number(var, label)
            if value is None:
                return False
            values[key] = value

        if need_height and values["height"] <= 0:
            messagebox.showerror(
                APP, "No strip height yet.\n\nRun Measure first, or type the "
                     "height in millimetres.", parent=self.root)
            return False
        if values["height"] and values["edge"] >= values["height"]:
            messagebox.showerror(
                APP, "Edge trim (%g mm) must be less than the strip height "
                     "(%g mm) or nothing is left to lift."
                     % (values["edge"], values["height"]), parent=self.root)
            return False

        s = self.session
        s.set_base(base)
        s.scan = scan
        out = self.v_out.get().strip()
        s.out_dir = out if out and os.path.isdir(out) else s.out_dir
        s.height, s.edge = values["height"], values["edge"]
        s.dx, s.dy = values["dx"], values["dy"]
        s.trial_pages = (self.v_trial.get() or "1-3").strip()
        s.replace_last = bool(self.v_replace.get())
        s.save()
        return True

    # -- the steps ---------------------------------------------------------

    def do_links(self):
        if not self._collect():
            return
        self._run("links", {"base": self.session.base,
                            "out_dir": self.session.out_dir or None},
                  "Checking links")

    def do_ghost(self):
        if not self._collect():
            return
        self._run("ghost", dict(self.session.files(),
                                pages=self.session.trial_pages),
                  "Building the ghost overlay")

    def do_measure(self):
        if not self._collect():
            return
        self._run("measure", self.session.files(), "Measuring")

    def do_ruler(self):
        if not self._collect():
            return
        if not self.session.height:
            self.log("No strip height yet, so the ruler will have no box on "
                     "it. Run Measure first if you want one.", "warn")
        self._run("ruler", dict(self.session.files(),
                                height=self.session.height or None,
                                edge=self.session.edge),
                  "Drawing the ruler")

    def do_trial(self):
        if not self._collect(need_height=True):
            return
        self._run("stamp", self.session.stamp_opts(trial=True), "Trial stamp")

    def do_full(self):
        if not self._collect(need_height=True):
            return
        s = self.session
        lines = [
            "About to stamp every page except the last.",
            "",
            "  Hyperlinked  %s" % os.path.basename(s.base),
            "  Scan         %s" % os.path.basename(s.scan),
            "  Strip        %g mm tall, %g mm trimmed off the edges"
            % (s.height, s.edge),
        ]
        if s.dx or s.dy:
            lines.append("  Nudge        dx %+g mm, dy %+g mm" % (s.dx, s.dy))
        lines.append("  Writes       %s_SIGNED.pdf"
                     % os.path.splitext(os.path.basename(s.base))[0])
        lines.append("")

        if s.replace_last:
            lines.append("The final page will be REPLACED with the scan's.")
            try:
                links = affstamp.final_page_links(s.base)
            except Exception:
                links = []
            if links:
                lines.append("")
                lines.append("WARNING: the final page carries %d hyperlink(s), "
                             "which that replacement will destroy:" % len(links))
                for target in links[:8]:
                    lines.append("    %s" % target)
                if len(links) > 8:
                    lines.append("    ... and %d more" % (len(links) - 8))
        else:
            lines.append("The final page will be left untouched.")
        lines.append("")
        lines.append("Go ahead?")

        if not messagebox.askyesno(APP, "\n".join(lines), parent=self.root):
            self.log("Full run cancelled.", "warn")
            return
        self._run("stamp", s.stamp_opts(trial=False), "Full stamp")

    def do_audit(self):
        if not self._collect():
            return
        self._run("audit", {"base": self.session.base,
                            "scan": self.session.scan}, "Auditing")

    def do_selftest(self):
        self._run("selftest", {}, "Self-test")

    # -- pickers and footer ------------------------------------------------

    def _start_dir(self) -> str:
        for path in (self.v_base.get(), self.v_scan.get(), self.v_out.get()):
            path = (path or "").strip()
            if path:
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                if os.path.isdir(folder):
                    return folder
        return os.path.expanduser("~")

    def _pick_pdf(self, title: str):
        return filedialog.askopenfilename(
            parent=self.root, title=title, initialdir=self._start_dir(),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])

    def pick_base(self):
        path = self._pick_pdf("Choose the hyperlinked PDF (the Word export)")
        if not path:
            return
        self.session.set_base(os.path.abspath(path))
        self.v_base.set(self.session.base)
        self.v_out.set(self.session.out_dir)
        self._show_tails()
        self.session.save()
        self.log("Hyperlinked PDF: %s" % self.session.base)
        self.log("Output folder:   %s" % self.session.out_dir)

    def pick_scan(self):
        path = self._pick_pdf("Choose the scan of the signed hardcopy")
        if not path:
            return
        self.session.scan = os.path.abspath(path)
        self.v_scan.set(self.session.scan)
        self._show_tails()
        self.session.save()
        self.log("Scan PDF: %s" % self.session.scan)

    def pick_out(self):
        folder = filedialog.askdirectory(parent=self.root,
                                         title="Choose the output folder",
                                         initialdir=self._start_dir())
        if not folder:
            return
        self.session.out_dir = os.path.abspath(folder)
        self.v_out.set(self.session.out_dir)
        self._show_tails()
        self.session.save()
        self.log("Output folder: %s" % self.session.out_dir)

    def open_folder(self):
        folder = (self.v_out.get() or "").strip()
        if not folder and self.v_base.get():
            folder = os.path.dirname(os.path.abspath(self.v_base.get()))
        if os.path.isdir(folder):
            open_in_explorer(folder)
        else:
            messagebox.showinfo(APP, "No output folder yet.", parent=self.root)

    def open_last(self):
        last = self.session.last_output
        if last and os.path.isfile(last):
            open_file(last)
        else:
            messagebox.showinfo(APP, "Nothing written yet in this session.",
                                parent=self.root)

    def save_log(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save the output log",
            initialdir=self._start_dir(), defaultextension=".txt",
            initialfile="affstamp_log.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text.get("1.0", "end-1c"))
            self.log("Saved the log to %s" % path, "ok")
        except OSError as exc:
            messagebox.showerror(APP, "Could not save the log:\n%s" % exc,
                                 parent=self.root)

    def clear_log(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def on_close(self):
        if self.busy and not messagebox.askyesno(
                APP, "A step is still running.\n\nClose anyway?",
                parent=self.root):
            return
        try:
            self._collect()
        except Exception:
            pass
        self.session.save()
        self.root.destroy()


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        sys.stderr.write("cannot open a window (%s).\n"
                         "Use AffStamp-cli.exe instead.\n" % exc)
        return 1
    AffStampWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
