#!/usr/bin/env python3
"""
pdflinkcheck - are a PDF's hyperlinks relative, and will they open in the PDF
viewer rather than a browser?

    PDFLinkCheck.exe bundle.pdf
    PDFLinkCheck.exe *.pdf
    PDFLinkCheck.exe C:\\Affidavit           (every PDF in the folder)
    PDFLinkCheck.exe bundle.pdf --fix        (rewrite them -> _fixed.pdf)

Or just double-click it and type a path, or drag a PDF onto the .exe.

Exit code 0 if everything passed, 1 if anything failed, 2 on a bad argument,
so it can gate a batch file:

    PDFLinkCheck.exe final.pdf --no-pause || echo STOP

One source file, needs only PyMuPDF, and ships as a self-contained .exe.


WHY THE ACTION TYPE MATTERS

A PDF link carries an action, and only one kind guarantees the PDF viewer:

  /GoToR   "go to remote" - opens the target IN THE SAME PDF VIEWER, at a
           given page. This is what an exhibit reference should be.
  /Launch  hands the file to the operating system's default handler. If Edge
           or Chrome owns .pdf - now the norm on Windows - the exhibit opens
           in a browser. Acrobat also warns that the file "may contain
           programs, macros, or viruses" before following it.
  /URI     goes to the browser, always. Wrong for a local exhibit.

And a target only travels if it is RELATIVE. Word writes absolute paths
(file:///C:/Users/...) whenever the Hyperlink Base is not set; those break
the moment the bundle is opened anywhere else.


WHY THIS READS THE RAW ANNOTATIONS

PyMuPDF's page.get_links() reports a /Launch action as kind 5 (GOTOR) - the
same value it reports for a real /GoToR. A checker built on it passes every
/Launch link silently, which is the exact fault this tool exists to catch.
So the action is read from the annotation object itself.
"""

import argparse
import os
import posixpath
import re
import sys

try:
    import pymupdf as fitz
except ImportError:                                     # older wheels
    import fitz                                         # type: ignore

VERSION = "1.1.0"

WEB = re.compile(r"^(https?|mailto|ftp|tel):", re.I)
DRIVE = re.compile(r"^/?[A-Za-z]:[\\/]")                # C:\x   /C:/x
UNC = re.compile(r"^(\\\\|//)[^/\\]")                   # \\server  //server
FILE_URI = re.compile(r"^file:", re.I)

STR_LIT = r"\(((?:[^()\\]|\\.)*)\)"
HEX_LIT = r"<([0-9A-Fa-f\s]+)>"


def pdf_text(literal, is_hex=False):
    """Decode a PDF string body into text."""
    if is_hex:
        raw = bytes.fromhex(re.sub(r"\s", "", literal))
        if raw[:2] == b"\xfe\xff":
            return raw[2:].decode("utf-16-be", "replace")
        return raw.decode("latin-1", "replace")
    out = re.sub(r"\\([()\\])", r"\1", literal)
    return out


def find_string(blob, key):
    """Value of /key as a string, whether written literal or hex."""
    m = re.search(r"/%s\s*%s" % (key, STR_LIT), blob)
    if m:
        return pdf_text(m.group(1))
    m = re.search(r"/%s\s*%s" % (key, HEX_LIT), blob)
    if m:
        return pdf_text(m.group(1), is_hex=True)
    return None


def read_links(doc, page):
    """Link annotations with their TRUE action, read from the raw object."""
    out = []
    for xref, atype, _ in page.annot_xrefs():
        if atype != fitz.PDF_ANNOT_LINK:
            continue
        try:
            blob = doc.xref_object(xref, compressed=True)
        except Exception:
            continue

        action = None
        m = re.search(r"/S\s*/(\w+)", blob)
        if m:
            action = "/" + m.group(1)
        elif "/Dest" in blob:
            action = "/GoTo"                            # destination, no action
        else:
            action = "(none)"

        target = (find_string(blob, "UF") or find_string(blob, "F")
                  or find_string(blob, "URI") or "")
        out.append({"xref": xref, "action": action, "target": target,
                    "page": page.number + 1})
    return out


def is_absolute(target):
    return bool(DRIVE.match(target) or UNC.match(target)
                or FILE_URI.match(target) or target.startswith("/"))


def classify(link, allow_launch=False):
    """-> (verdict, reason).  verdict is ok / warn / fail."""
    action, target = link["action"], link["target"]

    if action in ("/GoTo", "(none)"):
        return "ok", "internal jump - stays in this document"
    if action == "/Named":
        return "warn", "named action - check by hand what it does"
    if action == "/URI" and WEB.match(target):
        return "warn", "web link - fine if deliberate, but it opens a browser"

    problems = []
    if not target:
        problems.append("no target")
    elif is_absolute(target):
        problems.append("ABSOLUTE path - breaks on every other machine")
    elif "\\" in target:
        problems.append("backslashes - use forward slashes for portability")
    elif target.startswith("./"):
        problems.append("leading ./ - not part of the PDF file-specification "
                        "grammar, and some macOS viewers reject it")

    if action == "/GoToR":
        pass                                            # the one we want
    elif action == "/Launch":
        if not allow_launch:
            problems.append("/Launch - opens via the OS file association, so "
                            "a browser if Edge or Chrome owns .pdf")
    elif action == "/URI":
        problems.append("/URI on a local file - opens in a browser")
    else:
        problems.append("%s is not a file link action" % action)

    if problems:
        return "fail", "; ".join(problems)
    return "ok", "relative /GoToR - opens in the PDF viewer"


def resolve_target(target, base_dir):
    """Does this relative target actually exist next to the PDF?

    Returns (status, detail):
        found    it is there, spelled exactly as the link says
        case     it is there under a DIFFERENT CASE - works on Windows and on
                 a normal Mac volume, fails on a case-sensitive volume, a
                 network share, Linux, and many document management systems
        missing  nothing of that name is there at all
        skip     not a local relative path, so there is nothing to resolve

    The directory is listed rather than trusting os.path.isfile, because on a
    case-insensitive filesystem isfile() happily returns True for the wrong
    spelling - which is exactly how a bundle passes on Windows and then fails
    somewhere else.
    """
    if not target or WEB.match(target) or is_absolute(target):
        return "skip", ""

    parts = [p for p in target.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return "skip", ""

    cur, actual, exact = base_dir, [], True
    for part in parts:
        if part == "..":
            cur = os.path.dirname(cur)
            actual.append("..")
            continue
        try:
            entries = os.listdir(cur)
        except OSError:
            return "missing", posixpath.join(base_dir, "/".join(parts))
        if part in entries:
            match = part
        else:
            same = [e for e in entries if e.lower() == part.lower()]
            if not same:
                return "missing", posixpath.join(base_dir, "/".join(parts))
            match = same[0]
            exact = False
        actual.append(match)
        cur = os.path.join(cur, match)

    if not os.path.isfile(cur):
        return "missing", cur
    return ("found", "") if exact else ("case", "/".join(actual))


def relative_to(target, base_dir):
    """Absolute target -> path relative to base_dir, else the bare file name.

    A target that is ALREADY relative is returned untouched (bar backslashes).
    Running it through relpath would resolve it against the working directory
    and flatten "Exhibits/DOC-001.pdf" to "DOC-001.pdf", silently breaking a
    bundle whose exhibits live in a subfolder.
    """
    if not is_absolute(target):
        return re.sub(r"/{2,}", "/", target.replace("\\", "/"))

    raw = target
    if FILE_URI.match(raw):
        raw = re.sub(r"^[Ff][Ii][Ll][Ee]:/*", "", raw)
        try:
            from urllib.parse import unquote
            raw = unquote(raw)
        except Exception:
            pass
    raw = raw.replace("\\", "/")
    if re.match(r"^/[A-Za-z][:|]", raw):                # /C:/x -> C:/x
        raw = raw[1:]
    if re.match(r"^[A-Za-z]\|", raw):
        raw = raw.replace("|", ":", 1)

    def split(p):
        m = re.match(r"^([A-Za-z]):(.*)$", p)
        return (m.group(1).lower(), m.group(2) or "/") if m else ("", p)

    anchor = base_dir.replace("\\", "/")
    if not (re.match(r"^[A-Za-z]:", anchor) or anchor.startswith("/")):
        anchor = os.path.abspath(base_dir).replace("\\", "/")

    t_drive, t_path = split(raw)
    a_drive, a_path = split(anchor)
    name = posixpath.basename(t_path.rstrip("/")) or raw

    if t_drive != a_drive:
        return name                                     # other drive / machine
    rel = posixpath.relpath(posixpath.normpath(t_path or "/"),
                            posixpath.normpath(a_path or "/"))
    return name if rel.startswith("..") else rel


def check(path, args):
    """-> (total, failures)"""
    try:
        doc = fitz.open(path)
    except Exception as exc:
        print("%s\n  CANNOT OPEN: %s" % (os.path.basename(path), exc))
        return 0, 1
    if doc.needs_pass:
        print("%s\n  PASSWORD PROTECTED - cannot read the links"
              % os.path.basename(path))
        doc.close()
        return 0, 1

    base_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    groups, total = {}, 0
    for page in doc:
        for link in read_links(doc, page):
            total += 1
            verdict, reason = classify(link, args.allow_launch)

            # A link can be perfectly formed and still not open, because
            # nothing of that name is sitting where it points.
            if not args.no_resolve:
                status, detail = resolve_target(link["target"], base_dir)
                extra = ""
                if status == "missing":
                    extra = ("TARGET NOT FOUND beside this PDF - nothing of "
                             "that name is there, so the link cannot open")
                elif status == "case":
                    extra = ("CASE MISMATCH - on disk it is '%s'. Windows and "
                             "a stock Mac volume forgive this; a case-"
                             "sensitive volume, a network share or a document "
                             "management system will not" % detail)
                if extra:
                    reason = extra if verdict == "ok" else extra + "; " + reason
                    verdict = "fail"

            key = (verdict, link["action"], link["target"], reason)
            groups.setdefault(key, []).append(link["page"])

    fails = sum(len(v) for k, v in groups.items() if k[0] == "fail")
    warns = sum(len(v) for k, v in groups.items() if k[0] == "warn")

    print("%s   %d page%s, %d link%s"
          % (os.path.basename(path), doc.page_count,
             "" if doc.page_count == 1 else "s",
             total, "" if total == 1 else "s"))
    if not total:
        print("  NO LINK ANNOTATIONS AT ALL - if you expected some, the export "
              "dropped them (use Save As PDF, not Print to PDF)")
        doc.close()
        return 0, 1

    rank = {"fail": 0, "warn": 1, "ok": 2}
    for (verdict, action, target, reason), pages in sorted(
            groups.items(), key=lambda kv: (rank[kv[0][0]], kv[0][2])):
        if verdict == "ok" and not args.verbose:
            continue
        where = ("p%d" % pages[0] if len(pages) == 1
                 else "p%d +%d" % (pages[0], len(pages) - 1))
        print("  %-4s %-9s %-8s %s"
              % (verdict.upper() if verdict == "fail" else verdict,
                 where, action, target or "-"))
        print("       %s" % reason)

    verdict = "PASS" if not fails else "FAIL"
    print("  == %s: %d ok, %d failed, %d to look at"
          % (verdict, total - fails - warns, fails, warns))
    doc.close()
    return total, fails


def fix(path, args):
    """Rewrite failing local links as relative /GoToR -> <name>_fixed.pdf."""
    doc = fitz.open(path)
    base_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    changed = 0

    for page in doc:
        raw = {l["xref"]: l for l in read_links(doc, page)}
        # get_links() gives the rectangles; the raw read gives the true action.
        for link in page.get_links():
            info = raw.get(link.get("xref"))
            if not info:
                continue
            if classify(info, args.allow_launch)[0] != "fail":
                continue
            target = info["target"]
            if not target or WEB.match(target):
                continue
            rel = relative_to(target, base_dir)
            while rel.startswith("./"):
                rel = rel[2:]
            try:
                page.delete_link(link)
                page.insert_link({"kind": fitz.LINK_GOTOR, "from": link["from"],
                                  "file": rel, "page": 0,
                                  "to": fitz.Point(0, 0)})
                changed += 1
            except Exception as exc:
                print("  p%d: could not rewrite %s (%s)"
                      % (page.number + 1, target, exc))

    if not changed:
        print("  nothing that --fix can repair")
        doc.close()
        return 0

    # PyMuPDF writes /D[0/XYZ 0 0 0]. A top coordinate of 0 is the BOTTOM of
    # the page in PDF space, so the exhibit opens scrolled to the foot of
    # page 1, and a zoom of 0 is meaningless. /Fit shows the whole page.
    for page in doc:
        for xref, atype, _ in page.annot_xrefs():
            if atype != fitz.PDF_ANNOT_LINK:
                continue
            try:
                if "/GoToR" in doc.xref_object(xref, compressed=True):
                    doc.xref_set_key(xref, "A/D", "[0/Fit]")
            except Exception:
                pass

    out = os.path.splitext(path)[0] + "_fixed.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    print("  rewrote %d link(s) as relative /GoToR  ->  %s"
          % (changed, os.path.basename(out)))
    print("  now re-check it:  %s \"%s\""
          % (prog_name(), os.path.basename(out)))
    return changed


def prog_name():
    """How to invoke this program, as the user actually has it."""
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return "py " + os.path.basename(__file__)


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += [os.path.join(p, f) for f in sorted(os.listdir(p))
                    if f.lower().endswith(".pdf")
                    and not f.lower().endswith("_fixed.pdf")]
        else:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Check that a PDF's hyperlinks are relative and open in "
                    "the PDF viewer rather than a browser.")
    ap.add_argument("pdfs", nargs="+", help="PDF files, or a folder of them")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list the links that pass, too")
    ap.add_argument("--allow-launch", action="store_true",
                    help="accept /Launch (only if Acrobat is the default PDF "
                         "application on every machine that will open this)")
    ap.add_argument("--fix", action="store_true",
                    help="rewrite failing links as relative /GoToR, into "
                         "<name>_fixed.pdf")
    ap.add_argument("--no-resolve", action="store_true",
                    help="do not check that each target actually exists next "
                         "to the PDF (use when the exhibits are not to hand)")
    ap.add_argument("--no-pause", action="store_true",
                    help="do not wait for a keypress at the end (use this "
                         "when calling it from a batch file)")
    ap.add_argument("--version", action="version",
                    version="PDFLinkCheck %s" % VERSION)
    a = ap.parse_args(argv)

    files = collect(a.pdfs)
    if not files:
        print("no PDFs found")
        return 2

    total_fail = 0
    for i, path in enumerate(files):
        if i:
            print("")
        if not os.path.isfile(path):
            print("%s\n  NOT FOUND" % path)
            total_fail += 1
            continue
        _, fails = check(path, a)
        total_fail += fails
        if a.fix and fails:
            fix(path, a)

    if len(files) > 1:
        print("\n%d file(s), %d failing link(s)" % (len(files), total_fail))
    return 1 if total_fail else 0


def interactive():
    """No arguments: ask for a path. This is what double-clicking gives you."""
    print("=" * 70)
    print("PDFLinkCheck %s" % VERSION)
    print("=" * 70)
    print("Checks that a PDF\'s hyperlinks are relative, and that they open in")
    print("the PDF viewer rather than a browser.")
    print("")
    print("Give it a PDF, or a folder of them. You can also drag a PDF straight")
    print("onto the .exe, or paste a path below.")
    while True:
        print("")
        try:
            raw = input("PDF or folder (blank to quit): ").strip()
        except EOFError:
            return 0
        raw = raw.strip('"').strip("'")
        if not raw:
            return 0
        print("")
        code = main([raw])
        if code == 1:
            try:
                reply = input("\nRewrite the failing links as relative "
                              "/GoToR? [y/N]: ").strip().lower()
            except EOFError:
                reply = ""
            if reply.startswith("y"):
                print("")
                main([raw, "--fix"])


if __name__ == "__main__":
    _argv = sys.argv[1:]
    _code = interactive() if not _argv else main(_argv)
    # A double-clicked or drag-and-dropped .exe gets its own console window,
    # which would vanish with the results still in it.
    if getattr(sys, "frozen", False) and "--no-pause" not in _argv:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    sys.exit(_code)
