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

VERSION = "1.4.0"

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

        # /NewWindow decides whether the exhibit opens in its own window or
        # REPLACES the document you are reading. Absent means the reader
        # decides, from a per-machine preference - which is why the same
        # bundle behaves differently on different desks.
        m = re.search(r"/NewWindow\s+(true|false)", blob)
        new_window = (m.group(1) == "true") if m else None

        # Where it sits on the page, so the report reads top to bottom the
        # way the document does. PDF y runs upwards, so larger is higher.
        top = 0.0
        m = re.search(r"/Rect\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"
                      r"\s+([-\d.]+)", blob)
        if m:
            top = max(float(m.group(2)), float(m.group(4)))

        out.append({"xref": xref, "action": action, "target": target,
                    "new_window": new_window, "page": page.number + 1,
                    "top": top})
    return out


def is_absolute(target):
    return bool(DRIVE.match(target) or UNC.match(target)
                or FILE_URI.match(target) or target.startswith("/"))


OPEN_IN = ("viewer", "browser", "any")

POLICY = {
    "viewer": {
        "want": "/GoToR",
        "ok": ("/GoToR",),
        "why": "opens in the PDF viewer",
    },
    "browser": {
        "want": "/URI",
        "ok": ("/URI",),
        "why": "opens in the default browser",
    },
    "any": {
        "want": None,
        "ok": ("/GoToR", "/URI", "/Launch"),
        "why": "action type not checked",
    },
}


WINDOW = ("new", "same", "any")

WINDOW_WORD = {True: "a new window", False: "the same window", None: "unset"}


def window_problem(link, window):
    """Will this link replace the document the reader is looking at?"""
    if window == "any" or link["action"] not in ("/GoToR", "/Launch"):
        return None
    got = link.get("new_window")
    want = (window == "new")
    if got is want:
        return None
    if got is None:
        return ("/NewWindow unset - the reader decides, and where 'open "
                "cross-document links in same window' is ticked this REPLACES "
                "the document being read")
    return ("/NewWindow is %s, but this bundle wants %s"
            % (str(got).lower(), WINDOW_WORD[want]))


def classify(link, allow_launch=False, open_in="viewer", window="new"):
    """-> (verdict, reason).  verdict is ok / warn / fail.

    `open_in` is the policy the bundle is meant to follow:
      viewer   every local link must be /GoToR
      browser  every local link must be /URI
      any      leave the action type alone; only the paths are checked
    """
    action, target = link["action"], link["target"]
    rule = POLICY[open_in]

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

    if action not in ("/GoToR", "/Launch", "/URI"):
        problems.append("%s is not a file link action" % action)
    elif action == "/Launch" and open_in != "any" and not allow_launch:
        problems.append("/Launch - opens via the OS file association, so a "
                        "browser if Edge or Chrome owns .pdf")
    elif action not in rule["ok"]:
        if open_in == "viewer":
            problems.append("/URI on a local file - opens in a browser, not "
                            "the PDF viewer")
        else:
            problems.append("/GoToR opens in the PDF viewer - this bundle is "
                            "meant to open links in a browser")

    win = window_problem(link, window)
    if win:
        problems.append(win)

    if problems:
        return "fail", "; ".join(problems)
    tail = "" if window == "any" else ", opens in %s" % WINDOW_WORD[
        window == "new"]
    return "ok", "relative %s - %s%s" % (action, rule["why"], tail)


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


def _clean_absolute(target):
    """file:///C:/x%20y/z.pdf, /C:/x/z.pdf, C:\\x\\z.pdf -> C:/x y/z.pdf"""
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
    return raw


def locate_in_bundle(target, base_dir):
    """Where does an absolute target's file live in THIS bundle, if anywhere?

    Word records the path on the author's machine:
        file:///C:/Users/someone/Matter/Documents/DOC-005.pdf
    The bundle here is laid out the same way under some other folder. Try the
    longest trailing part of that path first - Matter/Documents/DOC-005.pdf,
    then Documents/DOC-005.pdf, then DOC-005.pdf - against the folder holding
    the PDF, and take the first that exists.

    Matching on the tail of the ORIGINAL path, rather than searching the
    bundle for any file of that name, means a superseded copy sitting in an
    Archive folder is never picked up by accident.

    Returns the relative path spelled as it is on disk, or None.
    """
    raw = posixpath.normpath(_clean_absolute(target))
    parts = [p for p in raw.split("/")
             if p and p != "." and not re.match(r"^[A-Za-z]:$", p)]
    for i in range(len(parts)):
        status, detail = resolve_target("/".join(parts[i:]), base_dir)
        if status == "found":
            return "/".join(parts[i:])
        if status == "case":
            return detail
    return None


def repair_target(target, base_dir, resolve=True):
    """Work out what a link's target should become.

    Returns (new_target, how) where how is a set drawn from:
        relative   an absolute path was made relative
        located    ...and the file was found in the bundle under part of its
                   original folder, rather than guessed at
        cased      the capitals were corrected to match the file on disk
        guessed    the file could not be found, so only the bare file name
                   is left - that link will not open until the file is there
    """
    how = set()
    rel = relative_to(target, base_dir)
    if is_absolute(target):
        how.add("relative")
        if resolve and resolve_target(rel, base_dir)[0] == "missing":
            found = locate_in_bundle(target, base_dir)
            if found:
                rel = found
                how.add("located")
    while rel.startswith("./"):
        rel = rel[2:]
    if resolve:
        status, detail = resolve_target(rel, base_dir)
        if status == "case":
            rel = detail
            how.add("cased")
        elif status == "missing" and "relative" in how:
            how.add("guessed")
    return rel, how


def relative_to(target, base_dir):
    """Absolute target -> path relative to base_dir, else the bare file name.

    A target that is ALREADY relative is returned untouched (bar backslashes).
    Running it through relpath would resolve it against the working directory
    and flatten "Exhibits/DOC-001.pdf" to "DOC-001.pdf", silently breaking a
    bundle whose exhibits live in a subfolder.
    """
    if not is_absolute(target):
        return re.sub(r"/{2,}", "/", target.replace("\\", "/"))

    raw = _clean_absolute(target)

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


OPENS = {"/GoToR": "PDF viewer", "/URI": "browser", "/Launch": "default app",
         "/GoTo": "this document", "(none)": "this document",
         "/Named": "named action"}
WINDOW_COL = {True: "new window", False: "SAME window", None: "reader decides"}
FILE_COL = {"found": "found", "case": "wrong case", "missing": "MISSING",
            "skip": "-", "unchecked": "not checked"}
POLICY_WORDS = {"viewer": "PDF viewer", "browser": "browser",
                "any": "any link type"}
WINDOW_WORDS = {"new": "new window", "same": "same window",
                "any": "any window setting"}


def describe(link, file_status):
    """(opens in, window, path, file) - plain words for one link."""
    action, target = link["action"], link["target"] or ""
    opens = OPENS.get(action, action)
    window = (WINDOW_COL[link.get("new_window")]
              if action in ("/GoToR", "/Launch") else "-")
    if action in ("/GoTo", "(none)"):
        path = "-"
    elif WEB.match(target):
        path = "web"
    elif not target:
        path = "none"
    elif is_absolute(target):
        path = "ABSOLUTE"
    else:
        path = "relative"
    return opens, window, path, FILE_COL.get(file_status, file_status)


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
    groups, total, rows = {}, 0, []
    for page in doc:
        for link in read_links(doc, page):
            total += 1
            file_status = "skip"
            verdict, reason = classify(link, args.allow_launch,
                                       args.open_in, args.window)

            # A link can be perfectly formed and still not open, because
            # nothing of that name is sitting where it points.
            if args.no_resolve:
                if (link["target"] and not WEB.match(link["target"])
                        and not is_absolute(link["target"])):
                    file_status = "unchecked"
            else:
                status, detail = resolve_target(link["target"], base_dir)
                file_status = status
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
            rows.append((verdict, link["page"], link, file_status))

    fails = sum(len(v) for k, v in groups.items() if k[0] == "fail")
    warns = sum(len(v) for k, v in groups.items() if k[0] == "warn")

    print("%s   %d page%s, %d link%s   [checking for: %s, %s]"
          % (os.path.basename(path), doc.page_count,
             "" if doc.page_count == 1 else "s",
             total, "" if total == 1 else "s",
             POLICY_WORDS[args.open_in], WINDOW_WORDS[args.window]))
    if total and not getattr(args, "brief", False):
        fmt = "  %-6s %-5s %-13s %-15s %-9s %-11s %s"
        rows.sort(key=lambda r: (r[1], -r[2].get("top", 0.0)))
        print("")
        print(fmt % ("STATUS", "PAGE", "OPENS IN", "WINDOW", "PATH", "FILE",
                     "TARGET"))
        for verdict_, pno, link, file_status in rows:
            opens, window, kind, found = describe(link, file_status)
            print(fmt % ({"fail": "FAIL", "warn": "warn", "ok": "ok"}[verdict_],
                         "p%d" % pno, opens, window, kind, found,
                         link["target"] or "-"))
        if any(r[0] != "ok" for r in rows) or args.verbose:
            print("")
            print("  PROBLEMS")
    if not total:
        print("  NO LINK ANNOTATIONS AT ALL - if you expected some, the export "
              "dropped them (use Save As PDF, not Print to PDF)")
        doc.close()
        return 0, 1

    rank = {"fail": 0, "warn": 1, "ok": 2}
    for (verdict, action, target, reason), pages in sorted(
            groups.items(), key=lambda kv: (rank[kv[0][0]], kv[0][2])):
        if getattr(args, "brief", False):
            break                       # the table was already shown
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
    notes = []
    resolve = not args.no_resolve

    for page in doc:
        raw = {l["xref"]: l for l in read_links(doc, page)}
        # get_links() gives the rectangles; the raw read gives the true action.
        for link in page.get_links():
            info = raw.get(link.get("xref"))
            if not info:
                continue
            target = info["target"]
            if not target or WEB.match(target):
                continue
            # The window setting is applied to every link afterwards, so it
            # alone is not a reason to rewrite one. Wrong capitals are: the
            # file is right there under its real name.
            wrong = classify(info, args.allow_launch, args.open_in,
                             "any")[0] == "fail"
            miscased = (resolve and not is_absolute(target)
                        and resolve_target(target, base_dir)[0] == "case")
            if not wrong and not miscased:
                continue
            rel, how = repair_target(target, base_dir, resolve)
            notes.append((page.number + 1, target, rel, how))

            if args.open_in == "browser":
                new = {"kind": fitz.LINK_URI, "from": link["from"], "uri": rel}
            elif args.open_in == "any":
                # Keep whatever action the link already had; only the path is
                # repaired. A /URI carries its target in "uri", not "file".
                if info["action"] == "/URI":
                    new = {"kind": fitz.LINK_URI, "from": link["from"],
                           "uri": rel}
                else:
                    kind = (fitz.LINK_GOTOR if info["action"] == "/GoToR"
                            else fitz.LINK_LAUNCH)
                    new = {"kind": kind, "from": link["from"], "file": rel}
                    if kind == fitz.LINK_GOTOR:
                        new["page"] = 0
                        new["to"] = fitz.Point(0, 0)
            else:
                new = {"kind": fitz.LINK_GOTOR, "from": link["from"],
                       "file": rel, "page": 0, "to": fitz.Point(0, 0)}
            try:
                page.delete_link(link)
                page.insert_link(new)
                changed += 1
            except Exception as exc:
                print("  p%d: could not rewrite %s (%s)"
                      % (page.number + 1, target, exc))

    windowed = apply_window(doc, args.window)
    changed_total = changed + windowed

    if not changed_total:
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
    if changed:
        print("  rewrote %d link(s) as %s"
              % (changed,
                 {"viewer": "relative /GoToR (opens in the PDF viewer)",
                  "browser": "relative /URI (opens in a browser)",
                  "any": "relative paths, action types unchanged"}[args.open_in]))
    if windowed:
        print("  pinned /NewWindow on %d link(s), so following one will not "
              "close the document being read" % windowed)

    located = [n for n in notes if "located" in n[3]]
    cased = [n for n in notes if "cased" in n[3]]
    if located:
        print("  found %d file(s) in the bundle under their original folder - "
              "check they are the right documents:" % len(located))
        for pno, old_t, new_t, _ in located[:25]:
            print("       p%-4d %s\n             -> %s" % (pno, old_t, new_t))
    if cased:
        print("  corrected the capitals on %d link(s) to match the file on disk"
              % len(cased))
    print("  wrote %s" % os.path.basename(out))

    if resolve:
        still = unresolved(out)
        if still:
            print("")
            print("  !! %d link(s) still point at a file that is not there. "
                  "--fix cannot supply a" % len(still))
            print("     missing document - put it in place, or correct the "
                  "link in the Word file:")
            for pno, tgt in still[:25]:
                print("       p%-4d %s" % (pno, tgt))
            if len(still) > 25:
                print("       ... and %d more" % (len(still) - 25))
    if not getattr(args, "brief", False):
        print("  now re-check it:  %s \"%s\""
              % (prog_name(), os.path.basename(out)))
    return changed


def unresolved(path):
    """[(page, target)] for every local link in `path` that does not resolve."""
    base_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    doc = fitz.open(path)
    out = []
    for page in doc:
        for link in read_links(doc, page):
            target = link["target"]
            if not target or WEB.match(target):
                continue
            if is_absolute(target):
                out.append((link["page"], target))
            elif resolve_target(target, base_dir)[0] != "found":
                out.append((link["page"], target))
    doc.close()
    return out


def prog_name():
    """How to invoke this program, as the user actually has it."""
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable)
    return "py " + os.path.basename(__file__)


def apply_window(doc, window):
    """Pin /NewWindow on every file link, so no reader has to guess.

    Left to itself, Acrobat follows its own preference, and a machine with
    "open cross-document links in same window" ticked will close the
    submission and replace it with the exhibit.
    """
    if window == "any":
        return 0
    value = "true" if window == "new" else "false"
    changed = 0
    for page in doc:
        for xref, atype, _ in page.annot_xrefs():
            if atype != fitz.PDF_ANNOT_LINK:
                continue
            try:
                blob = doc.xref_object(xref, compressed=True)
                if "/GoToR" not in blob and "/Launch" not in blob:
                    continue
                if re.search(r"/NewWindow\s+" + value, blob):
                    continue
                doc.xref_set_key(xref, "A/NewWindow", value)
                changed += 1
            except Exception:
                pass
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
    ap.add_argument("--open-in", choices=OPEN_IN, default="viewer",
                    metavar="{viewer,browser,any}",
                    help="how local links should open. viewer (default): "
                         "every one must be /GoToR, which opens the exhibit "
                         "in the PDF viewer. browser: every one must be /URI, "
                         "which hands it to the default browser. any: leave "
                         "the action types as they are and only check the "
                         "paths. --fix converts to whichever you choose.")
    ap.add_argument("--window", choices=WINDOW, default="new",
                    metavar="{new,same,any}",
                    help="whether an exhibit opens in its own window. new "
                         "(default): pin /NewWindow true, so following a link "
                         "never closes the document being read. same: pin it "
                         "false. any: do not check or change it, which leaves "
                         "the behaviour up to each reader's preferences.")
    ap.add_argument("--no-resolve", action="store_true",
                    help="do not check that each target actually exists next "
                         "to the PDF (use when the exhibits are not to hand)")
    ap.add_argument("--brief", action="store_true",
                    help=argparse.SUPPRESS)
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


def _ask_yes(question):
    try:
        return input("%s [y/N]: " % question).strip().lower().startswith("y")
    except EOFError:
        return False


def _ask_choice(question, options):
    """Numbered choice; Enter takes the first. Returns the chosen key."""
    print("")
    print(question)
    for i, (_, label) in enumerate(options, 1):
        print("  %d) %s" % (i, label))
    while True:
        try:
            raw = input("Choice [1]: ").strip()
        except EOFError:
            return options[0][0]
        if not raw:
            return options[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("  type a number from 1 to %d" % len(options))


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
        if code not in (0, 1):
            continue
        if not _ask_yes("\nRepair or change the links?"):
            continue

        open_in = _ask_choice(
            "How should the links open?",
            [("viewer", "in the PDF viewer          (recommended)"),
             ("browser", "in the web browser"),
             ("any", "leave the link types alone - only fix paths "
                     "and capitals")])
        window = "any"
        if open_in != "browser":
            window = _ask_choice(
                "Should an exhibit open in its own window?",
                [("new", "yes - never close the submission  (recommended)"),
                 ("same", "no - replace the submission with the exhibit"),
                 ("any", "leave it to each reader's own settings")])
        print("")
        main([raw, "--fix", "--open-in", open_in, "--window", window,
              "--brief", "--no-pause"])

        fixed = os.path.splitext(raw)[0] + "_fixed.pdf"
        if os.path.isfile(raw) and os.path.isfile(fixed):
            print("")
            print("-" * 70)
            print("THE FIXED FILE:")
            print("-" * 70)
            main([fixed, "--open-in", open_in, "--window", window,
                  "--no-pause"])


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
