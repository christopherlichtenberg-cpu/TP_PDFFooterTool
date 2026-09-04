#!/usr/bin/env python3
"""
pdflinkcheck - are a PDF's hyperlinks relative, and will they open in the PDF
viewer rather than a browser?

    py pdflinkcheck.py bundle.pdf
    py pdflinkcheck.py *.pdf
    py pdflinkcheck.py C:\\Affidavit           (every PDF in the folder)
    py pdflinkcheck.py bundle.pdf --fix        (rewrite them -> _fixed.pdf)

Exit code 0 if everything passed, 1 if anything failed, 2 on a bad argument,
so it can gate a process:  py pdflinkcheck.py final.pdf || echo STOP

One file, needs only PyMuPDF. Copy it wherever you need it.


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

    groups, total = {}, 0
    for page in doc:
        for link in read_links(doc, page):
            total += 1
            verdict, reason = classify(link, args.allow_launch)
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

    out = os.path.splitext(path)[0] + "_fixed.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    print("  rewrote %d link(s) as relative /GoToR  ->  %s"
          % (changed, os.path.basename(out)))
    print("  now re-check it:  py %s \"%s\""
          % (os.path.basename(__file__), os.path.basename(out)))
    return changed


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


if __name__ == "__main__":
    sys.exit(main())
