#!/usr/bin/env python3
"""
AffStamp - overlay the wet-ink signatures from a scanned affidavit onto a
hyperlinked PDF, without disturbing the hyperlinks.

A PDF hyperlink is an *annotation*: an object attached to the page, stored
separately from the content stream that holds the drawn marks.  Adding an
image to the content stream therefore cannot damage a link.  AffStamp only
ever adds, and saves incrementally, so the original Word export survives
byte-for-byte as a prefix of the output file.

Commands:

    affstamp                       text menu (no arguments)
    affstamp gui                   the window
    affstamp links     --base B                 list / repair link targets
    affstamp ghost     --base B --scan S        alignment check overlay
    affstamp measure   --scan S [--base B]      find the ink, suggest sizes
    affstamp ruler     --scan S                 mm grid + proposed box
    affstamp stamp     --base B --scan S ...    do the work
    affstamp audit     --base B --scan S        compare the wording
    affstamp selftest                           prove it runs on this machine
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone

try:                                    # pymupdf >= 1.24 prefers this name
    import pymupdf as fitz
except ImportError:                     # older wheels only ship "fitz"
    import fitz                         # type: ignore

from PIL import Image, ImageFilter

APP = "AffStamp"
VERSION = "1.2.0"

MMPT = 72.0 / 25.4                      # points per millimetre


def mm(v: float) -> float:
    """millimetres -> points"""
    return v * MMPT


def pt(v: float) -> float:
    """points -> millimetres"""
    return v / MMPT


# Ink extraction defaults.  Grey levels, 0 = black, 255 = white.
DEF_WHITE = 205         # lighter than this is paper: fully transparent
DEF_BLACK = 60          # darker than this is ink: fully opaque
DEF_SEARCH = 60.0       # mm of page bottom that "measure" looks at
DEF_DPI_MEASURE = 200
DEF_DPI_STAMP = 400
DEF_MARGIN = 4.0        # mm of headroom added to the suggested strip height

BAR = "=" * 70
RULE = "-" * 70


# ---------------------------------------------------------------------------
# output
#
# Everything the tool says goes through say()/warn()/fail()/good().  The CLI
# prints it; the GUI installs its own writer and colours the pane by level.
# Program output stays 7-bit ASCII so it renders on a cp1252 console.
# ---------------------------------------------------------------------------

def _default_writer(msg: str, level: str) -> None:
    print(msg)
    sys.stdout.flush()


def _default_asker(question: str, default: bool) -> bool:
    suffix = "y/n [y]" if default else "y/n [n]"
    try:
        reply = input("%s %s: " % (question, suffix)).strip().lower()
    except EOFError:
        return default
    if not reply:
        return default
    return reply.startswith("y")


_WRITER = _default_writer
_ASKER = _default_asker


def set_writer(fn) -> None:
    """Install a writer taking (message, level).

    level is one of: info, head, warn, error, ok.
    """
    global _WRITER
    _WRITER = fn or _default_writer


def set_asker(fn) -> None:
    """Install a yes/no prompt taking (question, default) -> bool."""
    global _ASKER
    _ASKER = fn or _default_asker


def say(msg: str = "", level: str = "info") -> None:
    _WRITER(msg, level)


def head(msg: str) -> None:
    _WRITER(msg, "head")


def warn(msg: str) -> None:
    _WRITER("!! " + msg, "warn")


def fail(msg: str) -> None:
    _WRITER("XX " + msg, "error")


def good(msg: str) -> None:
    _WRITER(msg, "ok")


def ask(question: str, default: bool = True) -> bool:
    return _ASKER(question, default)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_pages(spec, total: int):
    """'1-3,7' -> [0,1,2,6].  Empty spec means every page."""
    if not spec:
        return list(range(total))
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            lo, hi = part.split("-", 1)
            try:
                out += list(range(int(lo) - 1, int(hi)))
            except ValueError:
                raise ValueError("cannot read page range %r" % part)
        else:
            try:
                out.append(int(part) - 1)
            except ValueError:
                raise ValueError("cannot read page number %r" % part)
    seen, uniq = set(), []
    for p in out:
        if 0 <= p < total and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def parse_colour(s):
    """'black' or '#1a3fbb' -> (r, g, b) in 0..255"""
    if not s or str(s).strip().lower() == "black":
        return (0, 0, 0)
    s = str(s).strip().lstrip("#")
    if len(s) != 6:
        raise ValueError("colour must be 6 hex digits or 'black', got %r" % s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def load_offsets(path):
    """CSV: page,dx_mm,dy_mm,scan_page -> {base_page_number: row}"""
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {int(r["page"]): r for r in csv.DictReader(fh) if r.get("page")}


def open_pdf(path: str, label: str):
    if not path:
        raise ValueError("no %s PDF given" % label)
    if not os.path.isfile(path):
        raise ValueError("%s PDF not found: %s" % (label, path))
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ValueError("cannot open %s PDF (%s): %s" % (label, path, exc))
    if doc.page_count == 0:
        raise ValueError("%s PDF has no pages: %s" % (label, path))
    if doc.needs_pass:
        raise ValueError("%s PDF is password protected: %s" % (label, path))
    return doc


def warn_rotation(doc, label: str) -> None:
    rots = sorted({p.rotation for p in doc} - {0})
    if rots:
        warn("%s has rotated pages (%s). Normalise the rotation first or the "
             "strip will land on the wrong edge." % (label, rots))


def out_dir_for(base_path: str, override=None) -> str:
    if override:
        return os.path.abspath(override)
    return os.path.dirname(os.path.abspath(base_path)) or os.getcwd()


def stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


# ---------------------------------------------------------------------------
# saved session state
#
# %LOCALAPPDATA%\AffStamp\session.json on Windows, ~/.local/share/AffStamp
# elsewhere.  Never fatal: an unwritable location just means the tool starts
# with empty fields.
# ---------------------------------------------------------------------------

STATE_KEYS = ("base", "scan", "out_dir", "height", "edge", "dx", "dy",
              "trial_pages", "replace_last")


def state_path() -> str:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, APP, "session.json")


def load_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if k in STATE_KEYS}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        path = state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        keep = {k: v for k, v in state.items() if k in STATE_KEYS}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, indent=2)
    except Exception:
        pass        # a read-only profile is not a reason to stop working


def clear_state() -> None:
    try:
        os.remove(state_path())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# link annotations
# ---------------------------------------------------------------------------

def link_target(lk: dict) -> str:
    return lk.get("uri") or lk.get("file") or ""


def link_fingerprint(doc):
    """(page, kind, target) per link, in page order - the integrity check."""
    out = []
    for page in doc:
        for lk in page.get_links():
            out.append((page.number, lk.get("kind"), link_target(lk)))
    return out


ABS_WIN = re.compile(r"^[A-Za-z]:[\\/]")


def classify_link(lk: dict) -> str:
    """ABS, REL, WEB, INTERNAL or OTHER."""
    kind = lk.get("kind")
    if kind == fitz.LINK_GOTO:
        return "INTERNAL"
    target = link_target(lk)
    if not target:
        return "OTHER"
    low = target.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return "WEB"
    if low.startswith("file:") or ABS_WIN.match(target) or target.startswith(
            ("\\\\", "/")):
        return "ABS"
    return "REL"


def _from_file_url(target: str) -> str:
    """file:///C:/x/y.pdf -> C:/x/y.pdf ; leaves plain paths alone."""
    t = target
    if re.match(r"^/[A-Za-z][:|]", t):     # /C:/x -> C:/x
        t = t[1:]
    if t.lower().startswith("file:"):
        t = re.sub(r"^[Ff][Ii][Ll][Ee]:/*", "", t)
        try:
            from urllib.parse import unquote
            t = unquote(t)
        except Exception:
            pass
        if re.match(r"^/[A-Za-z][:|]", t):
            t = t[1:]
        if re.match(r"^[A-Za-z]\|", t):
            t = t.replace("|", ":", 1)
    return t.replace("\\", "/")


def relative_target(target: str, base_dir: str):
    """Absolute link target -> a path relative to the folder holding the PDF.

    A hyperlinked bundle only travels if every target sits inside it, so a
    relative path is accepted only when it does not climb out of the output
    folder.  Anything else falls back to the bare file name, which at least
    resolves when the exhibit is dropped in beside the affidavit.

    Windows drive letters are handled on any host, so the behaviour is the
    same whether this runs frozen on the Acrobat machine or under test.

    Returns (new_target, how), or (None, reason) if nothing sensible remains.
    """
    import posixpath

    raw = _from_file_url(target)
    if not raw:
        return None, "empty target"

    def split_drive(path):
        m = re.match(r"^([A-Za-z]):(.*)$", path)
        if m:
            return m.group(1).lower(), m.group(2) or "/"
        return "", path

    # Keep a genuine absolute path as it is; only resolve a relative one.
    # abspath() on a POSIX host would mangle "C:/Affidavit" into nonsense.
    anchor = (base_dir or "").replace("\\", "/")
    if not (re.match(r"^[A-Za-z]:", anchor) or anchor.startswith("/")):
        anchor = os.path.abspath(base_dir).replace("\\", "/")

    t_drive, t_path = split_drive(raw)
    a_drive, a_path = split_drive(anchor)
    name = posixpath.basename(t_path.rstrip("/"))

    if not name:
        return None, "no file name in target"
    if raw.startswith("//") or raw.startswith("\\\\"):
        return name, "file name only (was a UNC network path)"
    if t_drive and a_drive and t_drive != a_drive:
        return name, "file name only (was on drive %s:)" % t_drive.upper()
    if bool(t_drive) != bool(a_drive):
        # e.g. a Windows-authored bundle inspected somewhere else; there is
        # no honest way to relativise it.
        return name, "file name only (path is from another machine)"

    rel = posixpath.relpath(posixpath.normpath(t_path),
                            posixpath.normpath(a_path))
    if rel.startswith(".."):
        return name, "file name only (target sat outside the bundle folder)"
    return rel, "relative to the output folder"


def cmd_links(a) -> int:
    """List every link annotation, flag absolutised ones, offer to repair."""
    base = open_pdf(a.base, "hyperlinked")
    warn_rotation(base, "hyperlinked PDF")
    base_dir = out_dir_for(a.base, getattr(a, "out_dir", None))

    head(BAR)
    head("LINKS in %s  (%d pages)" % (os.path.basename(a.base), base.page_count))
    head(BAR)

    rows, counts = [], {}
    for page in base:
        for lk in page.get_links():
            cls = classify_link(lk)
            counts[cls] = counts.get(cls, 0) + 1
            rows.append((page.number + 1, cls, link_target(lk), lk))

    if not rows:
        warn("This PDF has no link annotations at all.")
        warn("If you expected links, the Word export dropped them - re-export "
             "with File > Save As > PDF, not Print to PDF.")
        base.close()
        return 1

    for pno, cls, target, _ in rows[:a.show]:
        say("  p%-4d %-9s %s" % (pno, cls, target or "(internal)"))
    if len(rows) > a.show:
        say("  ... %d more (raise --show to see them all)" % (len(rows) - a.show))

    say(RULE)
    say("  total %d:  %s" % (len(rows), ", ".join(
        "%s %d" % (k, v) for k, v in sorted(counts.items()))))

    n_abs = counts.get("ABS", 0)
    if not n_abs:
        good("No absolutised targets. The relative paths came through intact.")
        base.close()
        return 0

    warn("%d link(s) point at an absolute path." % n_abs)
    warn("Word does this when the Hyperlink Base is not set. They will break "
         "on any other machine.")

    if not (a.repair or ask("Rewrite them as relative paths?", True)):
        say("Left unchanged.")
        base.close()
        return 0

    fixed = failed = 0
    reasons = {}
    for page in base:
        for lk in page.get_links():
            if classify_link(lk) != "ABS":
                continue
            new, how = relative_target(link_target(lk), base_dir)
            if not new:
                warn("p%d: could not rewrite %s (%s)"
                     % (page.number + 1, link_target(lk), how))
                failed += 1
                continue
            reasons[how] = reasons.get(how, 0) + 1
            lk["kind"] = fitz.LINK_LAUNCH
            lk["file"] = new
            lk["uri"] = None
            try:
                page.update_link(lk)
                fixed += 1
            except Exception as exc:
                warn("p%d: update failed: %s" % (page.number + 1, exc))
                failed += 1

    out = os.path.join(base_dir, stem(a.base) + "_fixed.pdf")
    base.save(out, garbage=3, deflate=True)
    base.close()

    say(RULE)
    good("Rewrote %d link(s). Wrote %s" % (fixed, out))
    for how, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        line = "   %4d  %s" % (count, how)
        if how.startswith("file name only"):
            warn(line.strip() + " - check those resolve where the bundle "
                                "will be opened")
        else:
            say(line)
    if failed:
        warn("%d link(s) could not be rewritten - check them by hand." % failed)
    say("Use this file from here on; it replaces the hyperlinked PDF.")
    a.base = out
    return 0


# ---------------------------------------------------------------------------
# image analysis
#
# No numpy: PIL's BOX resize averages, which gives a row or column profile in
# one call and keeps the frozen build ~45 MB smaller.
# ---------------------------------------------------------------------------

def _grey_strip(page, search_mm: float, dpi: int, despeckle: bool):
    """Bottom `search_mm` of a page as an 8-bit greyscale PIL image."""
    r = page.rect
    clip = fitz.Rect(r.x0, r.y1 - mm(search_mm), r.x1, r.y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    if despeckle and min(img.size) >= 3:
        img = img.filter(ImageFilter.MedianFilter(3))
    return img, clip


def _row_profile(binary):
    """Mean ink per row, 0.0 .. 1.0 (binary must be 255 = ink)."""
    w, h = binary.size
    col = binary.resize((1, h), Image.BOX)
    return [v / 255.0 for v in col.tobytes()]


def _col_profile(binary):
    """Mean ink per column, 0.0 .. 1.0."""
    w, h = binary.size
    row = binary.resize((w, 1), Image.BOX)
    return [v / 255.0 for v in row.tobytes()]


def analyse_page(page, search_mm=DEF_SEARCH, dpi=DEF_DPI_MEASURE,
                 white_cut=DEF_WHITE, despeckle=True,
                 min_ink_frac=0.0015, gap_mm=2.0, edge_mm=0.0):
    """Ink bands near the foot of a page.

    Returns [(bottom_mm, top_mm, left_mm, right_mm), ...], measured up from
    the bottom edge and in from the left edge.  `edge_mm` of scanner shadow
    is ignored, so the black band a flatbed leaves does not read as ink.
    """
    grey, clip = _grey_strip(page, search_mm, dpi, despeckle)
    binary = grey.point(lambda v: 255 if v < white_cut else 0)
    w, h = binary.size
    if not w or not h:
        return []
    ppm_y = h / search_mm
    ppm_x = w / pt(clip.width)
    gap_rows = max(1, int(gap_mm * ppm_y))

    if edge_mm > 0:
        ex, ey = int(edge_mm * ppm_x), int(edge_mm * ppm_y)
        if w - 2 * ex < 4 or h - ey < 4:
            return []
        keep = Image.new("L", binary.size, 0)
        keep.paste(binary.crop((ex, 0, w - ex, h - ey)), (ex, 0))
        binary = keep

    rows = [y for y, frac in enumerate(_row_profile(binary))
            if frac >= min_ink_frac]
    if not rows:
        return []

    groups = [[rows[0]]]
    for y in rows[1:]:
        if y - groups[-1][-1] <= gap_rows:
            groups[-1].append(y)
        else:
            groups.append([y])

    bands = []
    for g in groups:
        top_row, bot_row = g[0], g[-1]
        bbox = binary.crop((0, top_row, w, bot_row + 1)).getbbox()
        left_mm = (bbox[0] / ppm_x) if bbox else 0.0
        right_mm = (bbox[2] / ppm_x) if bbox else pt(clip.width)
        bands.append(((h - 1 - bot_row) / ppm_y,      # bottom, mm up from foot
                      (h - 1 - top_row) / ppm_y,      # top,    mm up from foot
                      left_mm, right_mm))
    bands.sort()
    return bands


def detect_edge_trim(page, search_mm=25.0, dpi=100, dark_frac=0.55):
    """How many mm of near-solid scanner shadow hug the page edges.

    A flatbed scan of a page smaller than the platen leaves a black band.
    Lifting it would paint a bar across the foot of every page, so measure
    it and trim it off.
    """
    grey, clip = _grey_strip(page, search_mm, dpi, despeckle=False)
    dark = grey.point(lambda v: 255 if v < 128 else 0)
    w, h = dark.size
    if not w or not h:
        return 0.0
    ppm_y = h / search_mm
    ppm_x = w / pt(clip.width)

    rows = _row_profile(dark)
    bottom = 0
    for y in range(h - 1, -1, -1):
        if rows[y] >= dark_frac:
            bottom += 1
        else:
            break

    cols = _col_profile(dark)
    left = 0
    for x in range(w):
        if cols[x] >= dark_frac:
            left += 1
        else:
            break
    right = 0
    for x in range(w - 1, -1, -1):
        if cols[x] >= dark_frac:
            right += 1
        else:
            break

    worst = max(bottom / ppm_y, left / ppm_x, right / ppm_x)
    return 0.0 if worst < 0.3 else round(worst + 0.5, 1)


# ---------------------------------------------------------------------------
# measure
# ---------------------------------------------------------------------------

def cmd_measure(a) -> int:
    scan = open_pdf(a.scan, "scan")
    warn_rotation(scan, "scan PDF")
    pages = parse_pages(a.pages, scan.page_count)

    head(BAR)
    head("MEASURE %s  (%d pages)" % (os.path.basename(a.scan), scan.page_count))
    head("Ink in the bottom %.0f mm, measured UP from the page edge"
         % a.search)
    head(BAR)

    # The scanner shadow has to come off first, or it reads as a solid band
    # of ink across the foot of every page and swamps the real measurement.
    edge = 0.0
    for i in pages:
        edge = max(edge, detect_edge_trim(scan[i]))
    if edge:
        say("  (ignoring %.1f mm of scanner shadow at the page edges)" % edge)

    page_w = pt(scan[pages[0]].rect.width) if pages else 210.0
    top_overall = 0.0
    left_overall, right_overall = 1e9, 0.0
    all_bands = []
    found = 0

    for i in pages:
        bands = analyse_page(scan[i], a.search, a.dpi, a.white,
                             not a.no_despeckle, edge_mm=edge)
        if not bands:
            say("  p%-4d (nothing)" % (i + 1))
            continue
        found += 1
        all_bands.extend(bands)
        say("  p%-4d %s" % (i + 1, "   ".join(
            "%.1f-%.1fmm [x %.0f-%.0fmm]" % (b, t, l, r)
            for b, t, l, r in bands)))
        top_overall = max(top_overall, max(t for _, t, _, _ in bands))
        left_overall = min(left_overall, min(l for _, _, l, _ in bands))
        right_overall = max(right_overall, max(r for _, _, _, r in bands))

    if not found:
        say(RULE)
        fail("No ink found anywhere in the searched zone.")
        say("Raise --search (currently %.0f mm) or lower --white (currently %d)."
            % (a.search, a.white))
        scan.close()
        return 1

    height = int(top_overall + a.margin + 0.99)

    say(RULE)
    say("Highest ink found          : %.1f mm above the page edge" % top_overall)
    say("Horizontal extent          : %.0f - %.0f mm from the left edge"
        % (left_overall, right_overall))
    if edge:
        say("Scanner shadow at the edge : %.1f mm" % edge)
    good("SUGGESTED  --height %d --edge %g" % (height, edge))

    # Handwriting sits in a column, usually on the right. Printed footer text
    # runs in from the left margin. Where the two are separable, the tighter
    # box is the safer lift: it cannot cover a footer link.
    column = [b for b in all_bands if b[2] > 0.5 * page_w]
    if column and len(column) < len(all_bands):
        h2 = int(max(t for _, t, _, _ in column) + a.margin + 0.99)
        l2 = max(0.0, min(l for _, _, l, _ in column) - 3.0)
        r2 = min(page_w, max(r for _, _, _, r in column) + 3.0)
        say("")
        good("SUGGESTED (right-hand column only - safer)")
        good("   --height %d --edge %g --left %.0f --right %.0f"
             % (h2, edge, l2, r2))
        say("Bands starting right of %.0f mm look like a signature column."
            % (0.5 * page_w))
        say("Bands running in from the left margin are more likely printed")
        say("footer text, which your hyperlinked PDF regenerates for itself -")
        say("lifting those is what hides a footer link under scanned ink.")
    say("")
    say("The suggestion comes from the WORST page, not a typical one, because")
    say("signatures are placed by hand. If the top band is printed footer text")
    say("rather than handwriting, use the top of the handwriting band instead")
    say("and add about %.0f mm." % a.margin)
    say(RULE)

    a.measured_height = height
    a.measured_edge = edge

    if a.base:
        _measure_base_clash(a, height)

    scan.close()
    return 0


def _measure_base_clash(a, height: int) -> None:
    """What lives under the proposed strip in the hyperlinked PDF."""
    base = open_pdf(a.base, "hyperlinked")
    warn_rotation(base, "hyperlinked PDF")
    scan_pages = fitz.open(a.scan).page_count

    say("")
    head("BASE: %s  (%d pages)" % (os.path.basename(a.base), base.page_count))
    if base.page_count != scan_pages:
        warn("PAGE COUNT MISMATCH: hyperlinked %d, scan %d. Resolve this "
             "before stamping, or supply --offsets."
             % (base.page_count, scan_pages))

    content, clashes = [], []
    for i in range(base.page_count):
        page = base[i]
        bands = analyse_page(page, float(height), a.dpi, a.white, False)
        if bands:
            content.append((i + 1, max(t for _, t, _, _ in bands)))
        zone = fitz.Rect(page.rect.x0, page.rect.y1 - mm(height),
                         page.rect.x1, page.rect.y1)
        for lk in page.get_links():
            if fitz.Rect(lk["from"]).intersects(zone):
                clashes.append((i + 1, link_target(lk) or "(internal)"))

    if content:
        say("  Existing content in the bottom %d mm: %s%s"
            % (height,
               ", ".join("p%d (to %.1fmm)" % (p, h) for p, h in content[:10]),
               "" if len(content) <= 10 else ", ..."))
    else:
        say("  Nothing printed in the bottom %d mm of the hyperlinked PDF."
            % height)

    if clashes:
        warn("%d link(s) sit inside the strip zone." % len(clashes))
        warn("They would still work, but be hidden under the scanned ink - "
             "which reads as a bug.")
        for p, t in clashes[:10]:
            say("       p%d: %s" % (p, t))
        if len(clashes) > 10:
            say("       ... %d more" % (len(clashes) - 10))
        say("  -> lower --height, or use --left/--right to lift only the "
            "handwriting column.")
    else:
        good("  No link annotations fall inside the strip zone. Good.")
    base.close()


# ---------------------------------------------------------------------------
# ruler
# ---------------------------------------------------------------------------

def cmd_ruler(a) -> int:
    doc = open_pdf(a.scan, "scan")
    warn_rotation(doc, "scan PDF")
    pages = parse_pages(a.pages, doc.page_count)

    for i in pages:
        page = doc[i]
        r = page.rect
        for v in range(0, int(a.search) + 1, 5):
            y = r.y1 - mm(v)
            major = (v % 10 == 0)
            page.draw_line((r.x0, y), (r.x1, y), color=(1, 0, 0),
                           width=0.8 if major else 0.4)
            page.insert_text((r.x0 + 6, y - 2), "%d" % v,
                             fontsize=6, color=(1, 0, 0))
        if a.height:
            x0 = mm(a.left) if a.left is not None else r.x0 + mm(a.edge)
            x1 = mm(a.right) if a.right is not None else r.x1 - mm(a.edge)
            box = fitz.Rect(x0, r.y1 - mm(a.height), x1, r.y1 - mm(a.edge))
            page.draw_rect(box, color=(0, 0.6, 0), width=1.2)
            page.insert_text((x0 + 3, r.y1 - mm(a.height) - 3),
                             "lift: height %g mm, edge %g mm"
                             % (a.height, a.edge), fontsize=7,
                             color=(0, 0.6, 0))

    out = a.out or os.path.join(out_dir_for(a.base or a.scan, a.out_dir),
                                stem(a.scan) + "_RULER.pdf")
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    good("Wrote %s" % out)
    say("Open it in Acrobat, zoom the footer, and read off the millimetres.")
    say("Red lines every 5 mm from the bottom edge; the green box is what "
        "would be lifted.")
    a.last_output = out
    return 0


# ---------------------------------------------------------------------------
# ghost overlay - the alignment check
# ---------------------------------------------------------------------------

def cmd_ghost(a) -> int:
    base = open_pdf(a.base, "hyperlinked")
    scan = open_pdf(a.scan, "scan")
    warn_rotation(base, "hyperlinked PDF")
    warn_rotation(scan, "scan PDF")

    pages = parse_pages(a.pages or "1-3", base.page_count)
    out = a.out or os.path.join(out_dir_for(a.base, a.out_dir), "GHOST.pdf")

    ghost = fitz.open()
    for i in pages:
        if i >= scan.page_count:
            warn("no scan page %d - skipped" % (i + 1))
            continue
        bp = base[i]
        ghost.insert_pdf(base, from_page=i, to_page=i)
        page = ghost[-1]

        grey, _ = _grey_strip(scan[i], pt(scan[i].rect.height), a.dpi, False)
        alpha = grey.point([max(0, 200 - v) for v in range(256)])
        red = Image.new("RGB", grey.size, (220, 30, 30)).convert("RGBA")
        red.putalpha(alpha)
        buf = io.BytesIO()
        red.save(buf, format="PNG", optimize=True)
        page.insert_image(bp.rect, stream=buf.getvalue(), overlay=True,
                          keep_proportion=False)

    if ghost.page_count == 0:
        fail("Nothing to write.")
        return 1
    ghost.save(out, garbage=3, deflate=True)
    ghost.close(); base.close(); scan.close()

    good("Wrote %s  (%d page(s))" % (out, len(pages)))
    say("The scan is drawn in RED over the hyperlinked page.")
    say("Red text sitting exactly on the black text means the two documents")
    say("line up. A consistent offset is what --dx / --dy correct; a fan or")
    say("twist means the scan is skewed and needs deskewing first.")
    say("This file is a check only. Never send it to anyone.")
    a.last_output = out
    return 0


# ---------------------------------------------------------------------------
# stamp - the actual work
# ---------------------------------------------------------------------------

def build_alpha_lut(white: int, black: int):
    """Grey level -> opacity.  Paper vanishes, ink stays anti-aliased."""
    white = max(1, min(255, int(white)))
    black = max(0, min(white - 1, int(black)))
    span = max(1, white - black)
    return [0 if v >= white else
            255 if v <= black else
            int(round((white - v) * 255.0 / span))
            for v in range(256)]


def lift_strip(page, height, edge, left, right, dpi, lut, despeckle, ink_rgb):
    """Cut the ink out of the foot of a scanned page.

    Returns (png_bytes | None, clip_rect, coverage 0..1).  The background is
    genuinely transparent, so nothing already on the target page is painted
    over.
    """
    r = page.rect
    x0 = mm(left) if left is not None else r.x0 + mm(edge)
    x1 = mm(right) if right is not None else r.x1 - mm(edge)
    clip = fitz.Rect(max(x0, r.x0), r.y1 - mm(height),
                     min(x1, r.x1), r.y1 - mm(edge))
    if clip.width <= 1 or clip.height <= 1:
        return None, clip, 0.0

    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    if despeckle and min(img.size) >= 3:
        img = img.filter(ImageFilter.MedianFilter(3))

    alpha = img.point(lut)
    if alpha.getextrema()[1] == 0:
        return None, clip, 0.0
    coverage = sum(alpha.histogram()[i] * i for i in range(256)) / (
        255.0 * alpha.size[0] * alpha.size[1])

    rgba = Image.new("RGB", img.size, ink_rgb).convert("RGBA")
    rgba.putalpha(alpha)
    buf = io.BytesIO()
    rgba.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), clip, coverage


def final_page_links(path: str):
    """Link targets on the last page - the ones --replace-last would destroy."""
    doc = fitz.open(path)
    try:
        if doc.page_count == 0:
            return []
        return [link_target(lk) or "(internal)"
                for lk in doc[doc.page_count - 1].get_links()]
    finally:
        doc.close()


def cmd_stamp(a) -> int:
    if not a.height:
        fail("No strip height. Run measure first, or pass --height.")
        return 2

    base_path = os.path.abspath(a.base)
    out_dir = out_dir_for(a.base, a.out_dir)
    trial = bool(a.pages)
    if a.out:
        out_path = os.path.abspath(a.out)
        if not os.path.dirname(out_path):
            out_path = os.path.join(out_dir, os.path.basename(out_path))
    else:
        out_path = os.path.join(
            out_dir, "TEST.pdf" if trial else stem(a.base) + "_SIGNED.pdf")

    if os.path.abspath(out_path) == base_path:
        fail("The output would overwrite the hyperlinked PDF. Choose another "
             "name.")
        return 2

    scan = open_pdf(a.scan, "scan")
    probe = open_pdf(a.base, "hyperlinked")
    warn_rotation(probe, "hyperlinked PDF")
    warn_rotation(scan, "scan PDF")
    n = probe.page_count
    before = link_fingerprint(probe)
    base_sizes = [(p.rect.width, p.rect.height) for p in probe]
    last_page_links = [t for pno, _, t in before if pno == n - 1]
    probe.close()

    offsets = load_offsets(a.offsets)
    if scan.page_count != n and not offsets:
        fail("Page counts differ: hyperlinked %d, scan %d." % (n, scan.page_count))
        say("Supply --offsets with a scan_page column to map them explicitly.")
        scan.close()
        return 2

    head(BAR)
    head("STAMP  %s" % ("(trial run)" if trial else "(full run)"))
    head(BAR)
    say("  hyperlinked : %s  (%d pages)" % (os.path.basename(a.base), n))
    say("  scan        : %s  (%d pages)" % (os.path.basename(a.scan),
                                            scan.page_count))
    say("  strip       : height %g mm, edge trim %g mm%s"
        % (a.height, a.edge,
           "" if a.left is None else ", x %g-%g mm" % (a.left, a.right or 0)))
    if a.dx or a.dy:
        say("  nudge       : dx %+g mm, dy %+g mm" % (a.dx, a.dy))
    say("  ink         : %s at %d dpi" % (a.ink, a.dpi))
    say("  links found : %d" % len(before))
    say(RULE)

    # Copy first, then edit in place and save incrementally.  That is what
    # keeps the Word export byte-identical inside the output.
    original = open(base_path, "rb").read()
    shutil.copyfile(base_path, out_path)
    doc = fitz.open(out_path)

    lut = build_alpha_lut(a.white, a.black)
    ink_rgb = parse_colour(a.ink)
    despeckle = not a.no_despeckle

    # The last page carries the execution block, not a footer strip, so it is
    # never stamped - it is either left alone or replaced wholesale.
    targets = [p for p in parse_pages(a.pages, n) if p != n - 1]
    if not targets:
        warn("No pages to stamp (the final page is never stamped).")

    records, stamped, blank = [], 0, []
    for i in targets:
        row = offsets.get(i + 1, {})
        sp_no = int(row.get("scan_page") or (i + 1)) - 1
        if not (0 <= sp_no < scan.page_count):
            warn("p%d: no scan page %d - skipped" % (i + 1, sp_no + 1))
            continue

        bp, sp = doc[i], scan[sp_no]
        png, clip, coverage = lift_strip(sp, a.height, a.edge, a.left, a.right,
                                         a.dpi, lut, despeckle, ink_rgb)
        if png is None:
            blank.append(i + 1)
            warn("p%d: nothing lifted - the strip came out blank" % (i + 1))
            continue

        sx = 1.0 if a.no_scale else bp.rect.width / sp.rect.width
        sy = 1.0 if a.no_scale else bp.rect.height / sp.rect.height
        dx = mm(a.dx + float(row.get("dx_mm") or 0))
        dy = mm(a.dy + float(row.get("dy_mm") or 0))
        dst = fitz.Rect(
            bp.rect.x0 + (clip.x0 - sp.rect.x0) * sx + dx,
            bp.rect.y1 - (sp.rect.y1 - clip.y0) * sy + dy,
            bp.rect.x0 + (clip.x1 - sp.rect.x0) * sx + dx,
            bp.rect.y1 - (sp.rect.y1 - clip.y1) * sy + dy)

        bp.insert_image(dst, stream=png, overlay=True, keep_proportion=False)
        stamped += 1
        records.append({"page": i + 1, "scan_page": sp_no + 1,
                        "dest_mm": [round(pt(v), 2) for v in
                                    (dst.x0, dst.y0, dst.x1, dst.y1)],
                        "ink_coverage": round(coverage, 5)})
        say("  p%-4d from scan p%-4d %.0f x %.0f mm, %.2f%% ink"
            % (i + 1, sp_no + 1, pt(dst.width), pt(dst.height),
               coverage * 100))

    removed = []
    if a.replace_last:
        if scan.page_count < 1:
            warn("scan has no final page to substitute - skipped")
        else:
            w, h = base_sizes[n - 1]
            removed = list(last_page_links)
            doc.delete_page(n - 1)
            page = doc.new_page(-1, width=w, height=h)
            page.show_pdf_page(page.rect, scan, scan.page_count - 1)
            say(RULE)
            say("  final page  : replaced with scan p%d, resized to %.0f x %.0f mm"
                % (scan.page_count, pt(w), pt(h)))
            if removed:
                warn("that removed %d link annotation(s) that were on it:"
                     % len(removed))
                for t in removed[:10]:
                    say("       %s" % t)
    scan.close()

    incremental = True
    try:
        doc.save(out_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    except Exception as exc:
        incremental = False
        warn("incremental save refused (%s); writing a full copy instead. "
             "The links are still preserved, but the original bytes will not "
             "be a prefix of the output." % exc)
        doc.save(out_path, garbage=3, deflate=True)
    doc.close()

    return _verify_output(a, out_path, base_path, original, before, removed,
                          records, stamped, blank, incremental, trial, n)


def _verify_output(a, out_path, base_path, original, before, removed, records,
                   stamped, blank, incremental, trial, n) -> int:
    """Nothing is trusted until it has been read back off disk."""
    check = fitz.open(out_path)
    after = link_fingerprint(check)
    page_count = check.page_count
    check.close()

    report = os.path.splitext(out_path)[0] + "_links.txt"
    with open(report, "w", encoding="utf-8") as fh:
        for pno, kind, target in after:
            fh.write("p%d\t%s\t%s\n" % (pno + 1, kind, target))

    expected = [x for x in before
                if not (a.replace_last and x[0] == n - 1)]

    say(RULE)
    ok = True

    if after == expected:
        good("OK - all %d link annotations preserved." % len(after))
        if removed:
            say("   (%d link(s) went with the replaced final page, as asked.)"
                % len(removed))
    else:
        ok = False
        fail("LINK MISMATCH: expected %d, found %d. DO NOT USE THIS FILE."
             % (len(expected), len(after)))
        lost = [x for x in expected if x not in after]
        gained = [x for x in after if x not in expected]
        for pno, _, target in lost[:10]:
            say("   lost   p%d %s" % (pno + 1, target))
        for pno, _, target in gained[:10]:
            say("   gained p%d %s" % (pno + 1, target))

    if incremental:
        with open(out_path, "rb") as fh:
            prefix = fh.read(len(original))
        if prefix == original:
            good("OK - the hyperlinked PDF is unmodified inside the output "
                 "(byte-identical prefix).")
        else:
            ok = False
            fail("The original bytes are NOT intact. DO NOT USE THIS FILE.")

    if page_count != n:
        ok = False
        fail("Page count changed: %d in, %d out." % (n, page_count))

    if blank:
        warn("%d page(s) produced a blank strip: %s"
             % (len(blank), ", ".join("p%d" % p for p in blank[:15])))
        warn("Raise --white (try 215-225) if faint marks are being dropped.")

    manifest_path = os.path.splitext(out_path)[0] + ".manifest.json"
    manifest = {
        "tool": APP, "version": VERSION, "written": now_iso(),
        "run": "trial" if trial else "full",
        "inputs": {
            "hyperlinked": {"path": base_path, "sha256": sha256_file(base_path)},
            "scan": {"path": os.path.abspath(a.scan),
                     "sha256": sha256_file(a.scan)},
        },
        "output": {"path": out_path, "sha256": sha256_file(out_path),
                   "pages": page_count, "incremental_save": incremental},
        "settings": {"height_mm": a.height, "edge_mm": a.edge,
                     "left_mm": a.left, "right_mm": a.right,
                     "dx_mm": a.dx, "dy_mm": a.dy, "dpi": a.dpi,
                     "white": a.white, "black": a.black, "ink": a.ink,
                     "despeckle": not a.no_despeckle,
                     "scale_to_base": not a.no_scale,
                     "replace_last_page": bool(a.replace_last)},
        "pages_stamped": records,
        "pages_blank": blank,
        "links": {"before": len(before), "after": len(after),
                  "preserved": bool(after == expected)},
        "removed_with_final_page": removed,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    say(RULE)
    say("Stamped %d page(s)." % stamped)
    say("Wrote        %s" % out_path)
    say("Link report  %s" % report)
    say("Manifest     %s" % manifest_path)
    a.last_output = out_path

    if not ok:
        return 3

    if trial:
        say("")
        say("PRINT A PAGE OF THIS AND HOLD IT AGAINST THE ORIGINAL. That is "
            "the only reliable way to catch a small offset or scanner skew.")
        say("If the marks sit off by a millimetre or two, set a nudge "
            "(dx positive = right, dy positive = down) and run it again.")
    else:
        say("")
        say("Next: check it in Acrobat, then Certify it.")
        say("NEVER run Save As Optimized, Reduce File Size, Sanitize, or "
            "Print to PDF on this file - any of those will strip the links.")
    return 0


# ---------------------------------------------------------------------------
# audit - did the wording drift between the draft and what was signed?
# ---------------------------------------------------------------------------

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def cmd_audit(a) -> int:
    base = open_pdf(a.base, "hyperlinked")
    scan = open_pdf(a.scan, "scan")

    head(BAR)
    head("AUDIT - comparing the wording of the two documents")
    head(BAR)

    if base.page_count != scan.page_count:
        warn("Page counts differ: hyperlinked %d, scan %d."
             % (base.page_count, scan.page_count))

    scan_text = [_norm_text(p.get_text()) for p in scan]
    if sum(len(t) for t in scan_text) < 50 * scan.page_count:
        warn("The scan has no usable text layer, so the wording cannot be "
             "compared.")
        say("Run OCR on the SCAN first (never on the output), for example:")
        say("    ocrmypdf --deskew --clean signed_scan.pdf scan_ocr.pdf")
        say("then audit against scan_ocr.pdf. This step is optional; skip it "
            "if you are satisfied the draft and the signed copy match.")
        base.close(); scan.close()
        return 1

    worst, flagged = 1.0, []
    for i in range(min(base.page_count, scan.page_count)):
        b = _norm_text(base[i].get_text())
        s = scan_text[i]
        ratio = difflib.SequenceMatcher(None, b, s).ratio()
        worst = min(worst, ratio)
        marker = "ok" if ratio >= a.threshold else "DIFFERS"
        if ratio < a.threshold:
            flagged.append(i + 1)
        say("  p%-4d similarity %5.1f%%   %s" % (i + 1, ratio * 100, marker))

    say(RULE)
    if flagged:
        warn("%d page(s) differ by more than the threshold: %s"
             % (len(flagged), ", ".join("p%d" % p for p in flagged)))
        say("Some difference is normal - OCR misreads, and the scan has "
            "handwriting the draft does not. Read the flagged pages side by "
            "side before drawing any conclusion.")
    else:
        good("Every page is above %.0f%% similarity (worst %.1f%%)."
             % (a.threshold * 100, worst * 100))
    base.close(); scan.close()
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _fixture_base(path: str, pages: int = 3) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=mm(210), height=mm(297))
        page.insert_text((mm(25), mm(40)), "AFFIDAVIT OF A. WITNESS",
                         fontsize=14)
        for line in range(12):
            page.insert_text((mm(25), mm(55 + line * 7)),
                             "%d. The deponent says that document DOC-%03d is "
                             "true and correct." % (line + 1, i * 12 + line + 1),
                             fontsize=10)
        page.insert_text((mm(25), mm(280)), "DOC-%03d" % (i + 1), fontsize=9)
        page.insert_link({"kind": fitz.LINK_LAUNCH,
                          "file": "./Exhibits/DOC-%03d.pdf" % (i + 1),
                          "from": fitz.Rect(mm(25), mm(276), mm(60), mm(282))})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def _fixture_scan(path: str, pages: int = 3) -> None:
    """Same words, plus 'handwriting', flattened to images like a real scan."""
    build = fitz.open()
    for i in range(pages):
        page = build.new_page(width=mm(209.5), height=mm(296.5))
        page.insert_text((mm(25), mm(40)), "AFFIDAVIT OF A. WITNESS",
                         fontsize=14)
        for line in range(12):
            page.insert_text((mm(25), mm(55 + line * 7)),
                             "%d. The deponent says that document DOC-%03d is "
                             "true and correct." % (line + 1, i * 12 + line + 1),
                             fontsize=10)
        page.insert_text((mm(25), mm(280)), "DOC-%03d" % (i + 1), fontsize=9)
        # initials, bottom right
        x, y = mm(150), mm(285)
        for k in range(7):
            page.draw_bezier((x + k * mm(6), y),
                             (x + k * mm(6) + mm(2), y - mm(7)),
                             (x + k * mm(6) + mm(4), y + mm(4)),
                             (x + k * mm(6) + mm(6), y - mm(3)),
                             color=(0.05, 0.05, 0.05), width=1.6)
    flat = fitz.open()
    for i in range(pages):
        pix = build[i].get_pixmap(dpi=200, colorspace=fitz.csGRAY)
        page = flat.new_page(width=build[i].rect.width,
                             height=build[i].rect.height)
        page.insert_image(page.rect, pixmap=pix)
    flat.save(path, garbage=3, deflate=True)
    flat.close(); build.close()


def cmd_selftest(a) -> int:
    import tempfile
    head(BAR)
    head("%s %s SELF-TEST" % (APP, VERSION))
    head(BAR)
    say("python   %s" % sys.version.split()[0])
    say("pymupdf  %s" % getattr(fitz, "__version__", "?"))
    say("pillow   %s" % __import__("PIL").__version__)
    say("frozen   %s" % bool(getattr(sys, "frozen", False)))
    say(RULE)

    tmp = tempfile.mkdtemp(prefix="affstamp_selftest_")
    base_p = os.path.join(tmp, "base.pdf")
    scan_p = os.path.join(tmp, "scan.pdf")
    out_p = os.path.join(tmp, "out.pdf")
    try:
        _fixture_base(base_p)
        _fixture_scan(scan_p)
        say("built a 3 page fixture with 3 relative links")

        args = args_for("stamp", {"base": base_p, "scan": scan_p, "out": out_p,
                                  "height": 20, "edge": 1, "dpi": 300})
        rc = cmd_stamp(args)
        if rc != 0:
            fail("stamp returned %d" % rc)
            return 1

        before = link_fingerprint(fitz.open(base_p))
        after = link_fingerprint(fitz.open(out_p))
        if before != after:
            fail("links changed: %d -> %d" % (len(before), len(after)))
            return 1

        # the strip zone must actually be darker than it was
        b = fitz.open(base_p)[0]
        o = fitz.open(out_p)[0]
        zone = fitz.Rect(mm(140), mm(272), mm(205), mm(295))
        mean = lambda pg: sum(pg.get_pixmap(dpi=72, clip=zone,
                                            colorspace=fitz.csGRAY).samples)
        if not mean(o) < mean(b) * 0.999:
            fail("no ink landed on the page")
            return 1

        original = open(base_p, "rb").read()
        if open(out_p, "rb").read()[:len(original)] != original:
            fail("the base PDF bytes were not preserved")
            return 1

        say(RULE)
        good("SELFTEST PASSED - links preserved, ink stamped, base bytes "
             "intact.")
        return 0
    except Exception as exc:
        fail("SELFTEST FAILED: %s" % exc)
        for line in traceback.format_exc().splitlines():
            say("   " + line)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_gui(a) -> int:
    try:
        import affstamp_gui
    except ImportError as exc:
        # AffStamp-cli.exe is built without Tk. Hand over to the windowed
        # executable sitting beside it, if it is there.
        if getattr(sys, "frozen", False):
            beside = os.path.join(os.path.dirname(sys.executable),
                                  "AffStamp.exe" if os.name == "nt"
                                  else "AffStamp")
            if os.path.isfile(beside):
                import subprocess
                subprocess.Popen([beside])
                good("Opening the window (%s)." % os.path.basename(beside))
                return 0
        fail("cannot start the window: %s" % exc)
        say("Use the console menu instead: run this program with no arguments.")
        return 1
    return affstamp_gui.main()


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="affstamp",
        description="%s %s - overlay scanned signatures onto a hyperlinked "
                    "PDF without breaking the links." % (APP, VERSION))
    ap.add_argument("--version", action="version",
                    version="%s %s" % (APP, VERSION))
    sub = ap.add_subparsers(dest="cmd")

    def ink_opts(p):
        p.add_argument("--dpi", type=int, default=DEF_DPI_STAMP,
                       help="render resolution of the lifted strip")
        p.add_argument("--white", type=int, default=DEF_WHITE,
                       help="lighter than this is paper (raise if faint "
                            "marks vanish)")
        p.add_argument("--black", type=int, default=DEF_BLACK,
                       help="darker than this is solid ink")
        p.add_argument("--no-despeckle", action="store_true")

    p = sub.add_parser("links", help="list and repair link targets")
    p.add_argument("--base", required=True)
    p.add_argument("--out-dir")
    p.add_argument("--repair", action="store_true",
                   help="rewrite absolute targets without asking")
    p.add_argument("--show", type=int, default=40)
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("ghost", help="alignment check: scan drawn in red "
                                     "over the hyperlinked page")
    p.add_argument("--base", required=True)
    p.add_argument("--scan", required=True)
    p.add_argument("--out")
    p.add_argument("--out-dir")
    p.add_argument("--pages", default="1-3")
    p.add_argument("--dpi", type=int, default=150)
    p.set_defaults(func=cmd_ghost)

    p = sub.add_parser("measure", help="find the ink, suggest height and trim")
    p.add_argument("--scan", required=True)
    p.add_argument("--base", help="also check what the strip would cover")
    p.add_argument("--out-dir")
    p.add_argument("--pages")
    p.add_argument("--search", type=float, default=DEF_SEARCH)
    p.add_argument("--margin", type=float, default=DEF_MARGIN)
    p.add_argument("--dpi", type=int, default=DEF_DPI_MEASURE)
    p.add_argument("--white", type=int, default=DEF_WHITE)
    p.add_argument("--no-despeckle", action="store_true")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("ruler", help="mm grid and proposed box on the scan")
    p.add_argument("--scan", required=True)
    p.add_argument("--base", help="only used to choose the output folder")
    p.add_argument("--out")
    p.add_argument("--out-dir")
    p.add_argument("--pages", default="1-3")
    p.add_argument("--search", type=float, default=DEF_SEARCH)
    p.add_argument("--height", type=float)
    p.add_argument("--edge", type=float, default=0.0)
    p.add_argument("--left", type=float)
    p.add_argument("--right", type=float)
    p.set_defaults(func=cmd_ruler)

    p = sub.add_parser("stamp", help="lift the ink and lay it on the base")
    p.add_argument("--base", required=True)
    p.add_argument("--scan", required=True)
    p.add_argument("--out", help="default: <name>_SIGNED.pdf, or TEST.pdf "
                                 "when --pages is given")
    p.add_argument("--out-dir")
    p.add_argument("--height", type=float, required=True,
                   help="mm above the page edge that the strip reaches")
    p.add_argument("--edge", type=float, default=0.0,
                   help="mm of scanner shadow to trim off the edges")
    p.add_argument("--left", type=float, help="mm from the left edge")
    p.add_argument("--right", type=float, help="mm from the left edge")
    p.add_argument("--dx", type=float, default=0.0, help="nudge right, mm")
    p.add_argument("--dy", type=float, default=0.0, help="nudge down, mm")
    p.add_argument("--ink", default="black", help="'black' or a hex colour")
    p.add_argument("--pages", help="trial run: only these pages, e.g. 1-3")
    p.add_argument("--offsets", help="CSV: page,dx_mm,dy_mm,scan_page")
    p.add_argument("--replace-last", action="store_true",
                   help="swap the final page for the scan's, resized")
    p.add_argument("--no-scale", action="store_true")
    ink_opts(p)
    p.set_defaults(func=cmd_stamp)

    p = sub.add_parser("audit", help="compare the wording, page by page")
    p.add_argument("--base", required=True)
    p.add_argument("--scan", required=True)
    p.add_argument("--threshold", type=float, default=0.90)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("selftest", help="prove the tool works on this machine")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("gui", help="open the window")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("menu", help="the text menu (also the default)")
    p.set_defaults(func=lambda a: interactive())

    return ap


def args_for(cmd: str, opts: dict):
    """Build a parsed Namespace for `cmd` without going near sys.argv.

    Used by the menu and the window so that both get exactly the same
    defaults as the command line.
    """
    argv = [cmd]
    for key, value in opts.items():
        if value is None or value is False or value == "":
            continue
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        else:
            argv += [flag, str(value)]
    return build_parser().parse_args(argv)


# ---------------------------------------------------------------------------
# session - the settings the menu and the window both carry between steps
# ---------------------------------------------------------------------------

class Session(object):
    def __init__(self, load=True):
        s = load_state() if load else {}
        self.base = s.get("base") or ""
        self.scan = s.get("scan") or ""
        self.out_dir = s.get("out_dir") or ""
        self.height = s.get("height") or 0.0
        self.edge = s.get("edge") or 0.0
        self.dx = s.get("dx") or 0.0
        self.dy = s.get("dy") or 0.0
        self.trial_pages = s.get("trial_pages") or "1-3"
        self.replace_last = bool(s.get("replace_last", True))
        self.last_output = ""
        for attr in ("base", "scan"):
            if getattr(self, attr) and not os.path.isfile(getattr(self, attr)):
                setattr(self, attr, "")

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in STATE_KEYS}

    def save(self) -> None:
        save_state(self.as_dict())

    def files(self) -> dict:
        return {"base": self.base, "scan": self.scan,
                "out_dir": self.out_dir or None}

    def ready(self) -> bool:
        return bool(self.base and self.scan)

    def set_base(self, path: str) -> None:
        self.base = path
        if path and not self.out_dir:
            self.out_dir = os.path.dirname(os.path.abspath(path))

    def stamp_opts(self, trial: bool) -> dict:
        opts = dict(self.files())
        opts.update({"height": self.height, "edge": self.edge,
                     "dx": self.dx, "dy": self.dy,
                     "replace_last": (False if trial else self.replace_last)})
        if trial:
            opts["pages"] = self.trial_pages
        return opts

    def run(self, cmd: str, opts: dict) -> int:
        """Parse and run one command, absorbing anything it throws."""
        try:
            a = args_for(cmd, opts)
        except SystemExit:
            fail("bad arguments for %s" % cmd)
            return 2
        try:
            rc = a.func(a)
        except ValueError as exc:
            fail(str(exc))
            return 2
        except Exception as exc:
            fail("%s failed: %s" % (cmd, exc))
            for line in traceback.format_exc().splitlines():
                say("   " + line)
            return 2
        # carry forward whatever the command worked out
        if getattr(a, "measured_height", None):
            self.height = float(a.measured_height)
            self.edge = float(getattr(a, "measured_edge", 0.0) or 0.0)
        if cmd == "links" and getattr(a, "base", None) != self.base:
            self.set_base(a.base)
        if getattr(a, "last_output", None):
            self.last_output = a.last_output
        self.save()
        return rc


# ---------------------------------------------------------------------------
# the text menu
# ---------------------------------------------------------------------------

MENU = """
  1. Set the two PDFs
  2. Check / repair links
  3. Ghost overlay        (do the pages line up?)
  4. Measure              (how tall is the strip?)
  5. Ruler PDF            (see the millimetres)
  6. Trial stamp          (a few pages, then print one)
  7. FULL STAMP
  8. Audit                (optional: compare the wording)
  9. Self-test            (does this machine run the tool?)
  r. Reset saved settings
  0. Quit
"""


def _prompt(label: str, default: str = "") -> str:
    shown = " [%s]" % default if default else ""
    try:
        reply = input("%s%s: " % (label, shown)).strip().strip('"').strip("'")
    except EOFError:
        return default
    return reply or default


def _prompt_file(label: str, default: str = "") -> str:
    while True:
        path = _prompt(label, default)
        if not path:
            return ""
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            return os.path.abspath(path)
        fail("not found: %s" % path)


def _prompt_float(label: str, default: float) -> float:
    while True:
        raw = _prompt(label, "%g" % default)
        try:
            return float(raw)
        except ValueError:
            fail("that is not a number")


def _show_header(s: Session) -> None:
    say("")
    head(BAR)
    head("%s %s" % (APP, VERSION))
    head(BAR)
    say("  Hyperlinked PDF   %s" % (os.path.basename(s.base) if s.base
                                    else "(not set)"))
    say("  Scan PDF          %s" % (os.path.basename(s.scan) if s.scan
                                    else "(not set)"))
    say("  Output folder     %s" % (s.out_dir or "(follows the hyperlinked "
                                                 "PDF)"))
    if s.height:
        say("  Measured          --height %g --edge %g" % (s.height, s.edge))
    if s.dx or s.dy:
        say("  Nudge             --dx %g --dy %g" % (s.dx, s.dy))
    say("  Final page        %s" % ("replace with the scan's"
                                    if s.replace_last else "leave untouched"))
    say(BAR)


def interactive() -> int:
    s = Session()
    while True:
        _show_header(s)
        say(MENU)
        choice = _prompt("Choice").lower()

        if choice in ("0", "q", "quit", "exit"):
            return 0

        if choice == "r":
            clear_state()
            s = Session(load=False)
            good("Saved settings cleared.")
            continue

        if choice == "1":
            base = _prompt_file("Hyperlinked PDF (the Word export)", s.base)
            if base:
                s.set_base(base)
            scan = _prompt_file("Scan PDF (the signed hardcopy)", s.scan)
            if scan:
                s.scan = scan
            folder = _prompt("Output folder", s.out_dir)
            if folder:
                if os.path.isdir(folder):
                    s.out_dir = os.path.abspath(folder)
                else:
                    fail("not a folder: %s" % folder)
            s.save()
            continue

        if choice in ("2", "3", "4", "5", "6", "7", "8") and not s.ready():
            fail("Set the two PDFs first (option 1).")
            continue

        if choice == "2":
            s.run("links", {"base": s.base, "out_dir": s.out_dir or None})
        elif choice == "3":
            s.run("ghost", dict(s.files(), pages=s.trial_pages))
        elif choice == "4":
            s.run("measure", s.files())
        elif choice == "5":
            if not s.height:
                warn("No height yet - run Measure first, or the box will be "
                     "missing from the ruler.")
            s.run("ruler", dict(s.files(), height=s.height or None,
                                edge=s.edge))
        elif choice == "6":
            if not s.height:
                fail("No strip height yet. Run Measure (option 4) first.")
                continue
            s.trial_pages = _prompt("Trial pages", s.trial_pages)
            s.run("stamp", s.stamp_opts(trial=True))
            if ask("Set a nudge and try again?", False):
                s.dx = _prompt_float("dx mm (+ moves right)", s.dx)
                s.dy = _prompt_float("dy mm (+ moves down)", s.dy)
                s.save()
        elif choice == "7":
            if not s.height:
                fail("No strip height yet. Run Measure (option 4) first.")
                continue
            links = final_page_links(s.base)
            s.replace_last = ask("Replace the final page with the scan's?",
                                 s.replace_last)
            if s.replace_last and links:
                warn("The final page carries %d link(s), which the "
                     "replacement will destroy:" % len(links))
                for t in links[:10]:
                    say("       %s" % t)
                if not ask("Go ahead anyway?", False):
                    continue
            s.run("stamp", s.stamp_opts(trial=False))
        elif choice == "8":
            s.run("audit", {"base": s.base, "scan": s.scan})
        elif choice == "9":
            s.run("selftest", {})
        else:
            fail("Not a choice.")


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # Double-clicked, or run with no arguments: the menu, then wait so the
        # console window does not vanish before it can be read.
        try:
            rc = interactive()
        except KeyboardInterrupt:
            rc = 1
            say("")
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
        return rc

    parser = build_parser()
    a = parser.parse_args(argv)
    if not getattr(a, "func", None):
        parser.print_help()
        return 1
    try:
        return a.func(a)
    except ValueError as exc:
        fail(str(exc))
        return 2
    except KeyboardInterrupt:
        say("")
        fail("interrupted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
