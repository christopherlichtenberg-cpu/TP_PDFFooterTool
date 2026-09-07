#!/usr/bin/env python3
"""
AffStamp - overlay wet-ink signatures lifted from a scanned PDF onto a
hyperlinked PDF, without disturbing the link annotations.

Link annotations live in the page's /Annots array; drawn marks live in the
page's content stream. Adding an image to the content stream cannot affect
the annotations. Everything here is built on that separation.

Commands (run in this order):

  links    --base hyperlinked.pdf --dump
  links    --base hyperlinked.pdf --fix-relative --bundle-root . --out fixed.pdf
  compare  --base fixed.pdf --scan scan.pdf --out GHOST.pdf
  audit    --base fixed.pdf --scan scan.pdf --band 30
  measure  --scan scan.pdf --base fixed.pdf
  ruler    --scan scan.pdf --height 24
  stamp    --base fixed.pdf --scan scan.pdf --out FINAL.pdf --height 24

Run with no arguments for a menu.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import time
from collections import deque

try:
    import pymupdf
except ImportError:                     # PyMuPDF < 1.24.3
    import fitz as pymupdf

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# Pillow 9.1+ moved these onto enums; keep working on older builds.
try:
    _TRANSPOSE = Image.Transpose.TRANSPOSE
    _BOX = Image.Resampling.BOX
except AttributeError:                  # pragma: no cover
    _TRANSPOSE = Image.TRANSPOSE
    _BOX = Image.BOX

VERSION = "1.2.0"
BANNER = "=" * 72

MMPT = 72.0 / 25.4


def mm(v: float) -> float:
    """Millimetres -> PDF points."""
    return v * MMPT


def pt(v: float) -> float:
    """PDF points -> millimetres."""
    return v / MMPT


DEF_WHITE = 205         # grey level at/above which a pixel is "paper"
DEF_BLACK = 60          # grey level at/below which a pixel is solid ink
DEF_SEARCH = 60.0       # mm up from the foot that `measure` analyses
DEF_DPI = 200           # analysis resolution
DEF_STAMP_DPI = 400     # output resolution of the lifted strip
DEF_EDGE = 4.0          # mm of outer page edge ignored (scanner shadow)

# What `measure` last worked out, so the menu can offer it as a default.
LAST_SUGGESTED = {}

# The menu remembers the two file paths and the measured settings between
# runs. Best effort only - a read-only or roaming profile just means the
# menu starts empty, which is not an error.
STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA")
                         or os.path.expanduser("~"), "AffStamp")
STATE_FILE = os.path.join(STATE_DIR, "session.json")


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    except Exception:
        pass


def resolve_out(explicit, out_dir, anchor, filename):
    """Decide where an output PDF goes.

    --out wins outright; otherwise --out-dir; otherwise the folder holding
    `anchor` (the hyperlinked PDF for most commands).
    """
    if explicit:
        return explicit
    folder = out_dir or (os.path.dirname(os.path.abspath(anchor))
                         if anchor else "")
    return os.path.join(folder, filename) if folder else filename


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def open_pdf(path: str, label: str):
    if not path or not os.path.isfile(path):
        raise SystemExit(f"ERROR: {label} not found: {path!r}")
    doc = pymupdf.open(path)
    if doc.is_encrypted and not doc.authenticate(""):
        raise SystemExit(f"ERROR: {label} is password protected: {path!r}")
    return doc


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def warn_rotation(doc, label: str) -> None:
    rots = sorted({p.rotation for p in doc})
    if any(rots):
        print(f"!! {label}: rotated pages present {rots}. Normalise the page "
              f"rotation first, or the strip will land on the wrong edge.")


def warn_signed(doc, label: str) -> None:
    try:
        if doc.get_sigflags() >= 0:
            print(f"!! {label}: contains a digital signature field. A full "
                  f"save will invalidate it - use the default incremental save.")
    except Exception:
        pass


def parse_pages(spec, total):
    """'1-3,7,10-12' -> zero-based page indices. Empty spec -> all pages."""
    if not spec:
        return list(range(total))
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            s, e = part.split("-", 1)
            out += list(range(int(s) - 1, int(e)))
        else:
            out.append(int(part) - 1)
    seen, uniq = set(), []
    for p in out:
        if 0 <= p < total and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def parse_colour(s):
    if not s or s.lower() == "black":
        return (0, 0, 0)
    s = s.lstrip("#")
    if len(s) != 6:
        raise SystemExit(f"ERROR: --ink expects 'black' or a hex colour, got {s!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def load_offsets(path):
    """CSV with columns: page[,dx_mm][,dy_mm][,scan_page][,height]."""
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = {}
        for r in csv.DictReader(f):
            if not r.get("page"):
                continue
            rows[int(r["page"])] = r
        return rows


def alpha_lut(white: int, black: int):
    """Grey level -> alpha. Paper transparent, ink opaque, soft in between."""
    span = max(1, white - black)
    return [0 if v >= white else
            255 if v <= black else
            int(round((white - v) * 255.0 / span))
            for v in range(256)]


def render_gray(page, dpi, clip=None):
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def to_mask(gray, white_cut, despeckle=True):
    """Grey image -> 'L' mask where 255 == ink."""
    if despeckle:
        gray = gray.filter(ImageFilter.MedianFilter(3))
    return gray.point(lambda v: 255 if v < white_cut else 0)


def row_counts(mask):
    w, h = mask.size
    raw = mask.tobytes()
    return [raw[y * w:(y + 1) * w].count(255) for y in range(h)]


def col_counts(mask):
    return row_counts(mask.transpose(_TRANSPOSE))


def group_runs(idxs, max_gap):
    if not idxs:
        return []
    groups = [[idxs[0]]]
    for i in idxs[1:]:
        if i - groups[-1][-1] <= max_gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def blank_edges(mask, ppm_x, ppm_y, edge_mm, sides="lrb"):
    """Paint out the outer edge_mm so scanner shadow isn't read as ink."""
    if edge_mm <= 0:
        return mask
    w, h = mask.size
    ex, ey = int(edge_mm * ppm_x), int(edge_mm * ppm_y)
    d = ImageDraw.Draw(mask)
    if "l" in sides:
        d.rectangle([0, 0, ex, h], fill=0)
    if "r" in sides:
        d.rectangle([w - ex, 0, w, h], fill=0)
    if "b" in sides:
        d.rectangle([0, h - ey, w, h], fill=0)
    if "t" in sides:
        d.rectangle([0, 0, w, ey], fill=0)
    return mask


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
def bottom_bands(page, search_mm, dpi, white_cut, despeckle=True,
                 edge_mm=DEF_EDGE, min_ink_mm=1.5, gap_mm=2.5):
    """Horizontal ink bands in the bottom `search_mm` of a page.

    Returns a list of dicts with bottom/top (mm up from the page foot) and
    left/right (mm from the left page edge).
    """
    r = page.rect
    clip = pymupdf.Rect(r.x0, r.y1 - mm(search_mm), r.x1, r.y1) & r
    mask = to_mask(render_gray(page, dpi, clip), white_cut, despeckle)
    w, h = mask.size
    if not w or not h:
        return []
    ppm_x = w / pt(clip.width)
    ppm_y = h / pt(clip.height)
    mask = blank_edges(mask, ppm_x, ppm_y, edge_mm)

    min_ink = max(2, int(min_ink_mm * ppm_x))
    rows = [y for y, c in enumerate(row_counts(mask)) if c >= min_ink]
    bands = []
    for g in group_runs(rows, max(1, int(gap_mm * ppm_y))):
        top_row, bot_row = g[0], g[-1]
        sub = mask.crop((0, top_row, w, bot_row + 1))
        bbox = sub.getbbox()
        if not bbox:
            continue
        bands.append({
            "bottom": (h - 1 - bot_row) / ppm_y,
            "top":    (h - 1 - top_row) / ppm_y,
            "left":   bbox[0] / ppm_x,
            "right":  bbox[2] / ppm_x,
            "px":     sum(row_counts(sub)),
        })
    bands.sort(key=lambda b: b["bottom"])
    return bands


def clusters(mask, ppm_x, ppm_y, cell_mm=2.0, min_cells=3, fill_frac=0.06):
    """Connected blobs of ink, found on a coarse cell grid (no scipy).

    Returns [(x0_mm, y0_mm, x1_mm, y1_mm, cells)] in image coordinates,
    y measured DOWN from the top of the mask.
    """
    w, h = mask.size
    cw = max(1, int(round(cell_mm * ppm_x)))
    ch = max(1, int(round(cell_mm * ppm_y)))
    gx, gy = max(1, w // cw), max(1, h // ch)
    small = mask.resize((gx, gy), _BOX)
    thr = max(1, int(255 * fill_frac))
    px = small.load()
    seen = [[False] * gx for _ in range(gy)]
    out = []
    for j in range(gy):
        for i in range(gx):
            if seen[j][i] or px[i, j] < thr:
                continue
            q = deque([(i, j)])
            seen[j][i] = True
            cells = []
            while q:
                a, b = q.popleft()
                cells.append((a, b))
                for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    na, nb = a + da, b + db
                    if 0 <= na < gx and 0 <= nb < gy and not seen[nb][na] \
                            and px[na, nb] >= thr:
                        seen[nb][na] = True
                        q.append((na, nb))
            if len(cells) < min_cells:
                continue
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            out.append((min(xs) * cw / ppm_x, min(ys) * ch / ppm_y,
                        (max(xs) + 1) * cw / ppm_x, (max(ys) + 1) * ch / ppm_y,
                        len(cells)))
    out.sort(key=lambda c: -c[4])
    return out


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------
def link_target(lk):
    return lk.get("uri") or lk.get("file") or lk.get("name") or ""


def link_fingerprint(doc):
    """Identity of every link annotation, for a before/after comparison."""
    out = []
    for p in doc:
        for lk in p.get_links():
            out.append((p.number, lk.get("kind"), link_target(lk),
                        lk.get("page", -1),
                        tuple(round(v, 1) for v in lk["from"])))
    return out


def uri_to_path(target):
    """Return an absolute filesystem path if `target` is one, else None.

    Handles the shapes Word and PyMuPDF actually produce:
      file:///C:/x/y.pdf    ->  C:/x/y.pdf
      /C:/x/y.pdf           ->  C:/x/y.pdf   (Launch action, leading slash)
      a Windows drive path  ->  C:/x/y.pdf
      //server/share/y.pdf  ->  unchanged
    A relative target such as ./Exhibits/y.pdf returns None.
    """
    from urllib.parse import unquote, urlparse
    if not target:
        return None
    low = target.lower()
    if low.startswith(("http:", "https:", "mailto:", "ftp:")):
        return None
    if low.startswith("file:"):
        u = urlparse(target)
        path = unquote(u.path).replace("\\", "/")
        if u.netloc:
            return "//" + u.netloc + path
    else:
        path = target.replace("\\", "/")
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]                          # "/C:/x" -> "C:/x"
    if len(path) > 1 and path[1] == ":":
        return path                              # drive-absolute
    if path.startswith("//"):
        return path                              # UNC
    if path.startswith("/") and low.startswith("file:"):
        return path
    return None

def cmd_links(a):
    doc = open_pdf(a.base, "base")
    warn_rotation(doc, "base")

    rows = []
    for p in doc:
        for lk in p.get_links():
            rows.append((p.number + 1, lk.get("kind"), link_target(lk)))

    print(BANNER)
    print(os.path.basename(a.base) + ": " + str(doc.page_count) +
          " pages, " + str(len(rows)) + " link annotation(s)")
    print(BANNER)

    absolute = [r for r in rows if uri_to_path(r[2])]
    if a.dump or not (a.fix_relative or a.check):
        for pno, kind, tgt in rows:
            flag = "ABS" if uri_to_path(tgt) else "   "
            print("  %s p%3d  kind=%s  %s" % (flag, pno, kind, tgt))
    if absolute:
        print("\n!! %d link(s) point at an absolute path. These break the "
              "moment the bundle moves." % len(absolute))
        print("   Re-run with: --fix-relative --bundle-root <folder> --out <file>")
    else:
        print("\nNo absolute paths found.")

    if a.fix_relative:
        if not a.out:
            raise SystemExit("ERROR: --fix-relative needs --out")
        if os.path.abspath(a.out) == os.path.abspath(a.base):
            raise SystemExit("ERROR: --out must differ from --base")
        root = os.path.abspath(a.bundle_root or os.path.dirname(a.base) or ".")
        fixed = 0
        for p in doc:
            for lk in p.get_links():
                tgt = link_target(lk)
                path = uri_to_path(tgt)
                if not path:
                    continue
                ap = os.path.abspath(path)
                try:
                    rel = os.path.relpath(ap, root).replace("\\", "/")
                    if rel.startswith("..") and not a.allow_parent:
                        rel = os.path.basename(ap)
                except ValueError:
                    rel = os.path.basename(ap)
                if not rel.startswith((".", "/")):
                    rel = "./" + rel
                kind = (pymupdf.LINK_GOTOR if rel.lower().endswith(".pdf")
                        else pymupdf.LINK_LAUNCH)
                new = {"kind": kind, "from": pymupdf.Rect(lk["from"]),
                       "file": rel}
                if kind == pymupdf.LINK_GOTOR:
                    pg = lk.get("page", 0)
                    new["page"] = int(pg) if isinstance(pg, int) and pg > 0 else 0
                    new["to"] = pymupdf.Point(0, 0)
                p.delete_link(lk)
                p.insert_link(new)
                fixed += 1
                print("   p%d: %s\n        -> %s" % (p.number + 1, tgt, rel))
        doc.save(a.out, garbage=3, deflate=True)
        print("\nRewrote %d link(s). Wrote %s" % (fixed, a.out))
        print("Use THIS file as --base from here on.")
        doc.close()
        doc = open_pdf(a.out, "fixed")

    if a.check:
        root = os.path.abspath(a.bundle_root or
                               os.path.dirname(a.out or a.base) or ".")
        missing = []
        for p in doc:
            for lk in p.get_links():
                if lk.get("kind") not in (pymupdf.LINK_GOTOR, pymupdf.LINK_LAUNCH):
                    continue
                tgt = link_target(lk)
                absolute = uri_to_path(tgt)
                cand = (os.path.normpath(absolute) if absolute
                        else os.path.normpath(os.path.join(root, tgt)))
                if not os.path.exists(cand):
                    missing.append((p.number + 1, tgt))
        print(BANNER)
        if missing:
            print("!! %d link target(s) do not exist under %s:" %
                  (len(missing), root))
            for pno, tgt in missing:
                print("     p%d: %s" % (pno, tgt))
        else:
            print("All file link targets resolve under " + root + ". Good.")
    return 0


# ---------------------------------------------------------------------------
# measure
# ---------------------------------------------------------------------------
def _overlaps(band, others, tol=2.0):
    """True if `band` lines up vertically with any band in `others`.

    Compared against the scan band's own height, so a short printed line
    still matches even when the base band is measured slightly differently.
    """
    h = max(0.5, band["top"] - band["bottom"])
    for o in others:
        lo = max(band["bottom"] - tol, o["bottom"])
        hi = min(band["top"] + tol, o["top"])
        if hi - lo >= 0.5 * h:
            return True
    return False



def cmd_measure(a):
    scan = open_pdf(a.scan, "scan")
    warn_rotation(scan, "scan")
    pages = parse_pages(a.pages, scan.page_count)

    base = None
    if a.base:
        base = open_pdf(a.base, "base")
        warn_rotation(base, "base")

    sz = scan[0].rect
    print(BANNER)
    print("SCAN: %s  (%d pages)" % (os.path.basename(a.scan), scan.page_count))
    print("Page size: %.1f x %.1f mm" % (pt(sz.width), pt(sz.height)))
    print("Ink in the bottom %.0f mm (mm measured UP from the foot)." % a.search)
    if base:
        print("Bands the Word export also has are marked 'printed' - those are")
        print("regenerated on the base page, so you do NOT want to lift them.")
    print(BANNER)

    tall_hand, tall_hand_pg = 0.0, None
    tall_any, tall_any_pg = 0.0, None
    left_x, right_x = 1e9, 0.0
    blank = []
    for i in pages:
        bands = bottom_bands(scan[i], a.search, a.dpi, a.white,
                             not a.no_despeckle, a.edge)
        printed = []
        if base and i < base.page_count:
            printed = bottom_bands(base[i], a.search, a.dpi, a.white,
                                   False, a.edge)
        hand = [b for b in bands if not _overlaps(b, printed)]
        if not hand:
            blank.append(i + 1)
        if not bands:
            print("  p%3d: (no ink found)" % (i + 1))
            continue

        bits = []
        for b in bands:
            tag = "" if b in hand else " printed"
            bits.append("%.1f-%.1fmm [x %.0f-%.0f]%s"
                        % (b["bottom"], b["top"], b["left"], b["right"], tag))
        print("  p%3d: " % (i + 1) + "   ".join(bits))

        hi = max(b["top"] for b in bands)
        if hi > tall_any:
            tall_any, tall_any_pg = hi, i + 1
        if hand:
            hh = max(b["top"] for b in hand)
            if hh > tall_hand:
                tall_hand, tall_hand_pg = hh, i + 1
            left_x = min(left_x, min(b["left"] for b in hand))
            right_x = max(right_x, max(b["right"] for b in hand))

    print(BANNER)
    if tall_any == 0:
        print("No ink detected. Raise --search, or lower --white (try 220).")
        return 1

    if base and tall_hand:
        rec = int(tall_hand + a.margin + 0.999)
        print("Tallest band unique to the scan : %.1f mm  (page %s)"
              % (tall_hand, tall_hand_pg))
        print("Tallest band of any kind        : %.1f mm  (page %s)"
              % (tall_any, tall_any_pg))
        print("Horizontal spread of that ink   : %.0f - %.0f mm from the left"
              % (left_x, right_x))
    else:
        rec = int(tall_any + a.margin + 0.999)
        print("Highest ink anywhere            : %.1f mm  (page %s)"
              % (tall_any, tall_any_pg))
        print("Horizontal spread of ink        : %.0f - %.0f mm from the left"
              % (left_x if left_x < 1e8 else 0, right_x))
    if blank:
        print("!! Pages with NO handwriting at the foot: %s"
              % ", ".join(str(p) for p in blank))
        print("   Every page should carry both marks. Check those pages.")
    print("")
    LAST_SUGGESTED["height"] = rec
    print("SUGGESTED  --height %d" % rec)
    print("  (tallest %s band %.1f mm + %.0f mm margin, rounded up)"
          % ("handwriting" if base and tall_hand else "ink",
             tall_hand if (base and tall_hand) else tall_any, a.margin))
    if not base:
        print("  Re-run with --base to separate handwriting from printed text;")
        print("  without it this number includes any printed footer line.")
    print("  Signatures are hand-placed, so this is set by the WORST page.")
    print("  Run `audit` next, to confirm nothing sits above it.")

    # Scanner shadow along the page edge would be lifted onto every page.
    raw = bottom_bands(scan[pages[0]], min(a.search, 12.0), a.dpi, a.white,
                       not a.no_despeckle, 0.0)
    shadow = [b for b in raw if b["bottom"] < 2.0
              and (b["right"] - b["left"]) > 0.6 * pt(sz.width)]
    if shadow:
        print("")
        print("!! A dark band runs along the very bottom edge of the scan")
        print("   (%.1f-%.1f mm, nearly full width). That is scanner shadow."
              % (shadow[0]["bottom"], shadow[0]["top"]))
        print("   It would be stamped onto every page as a black bar.")
        LAST_SUGGESTED["edge"] = max(2, int(shadow[0]["top"] + 1.5))
        print("   Add  --edge %d  to the stamp command to discard it."
              % LAST_SUGGESTED["edge"])
    print(BANNER)

    if base:
        bsz = base[0].rect
        print("")
        print("BASE: %s  (%d pages)" % (os.path.basename(a.base),
                                        base.page_count))
        print("Page size: %.1f x %.1f mm" % (pt(bsz.width), pt(bsz.height)))
        if base.page_count != scan.page_count:
            print("  !! PAGE COUNT MISMATCH (%d vs %d). Resolve before stamping."
                  % (base.page_count, scan.page_count))
        if abs(bsz.width - sz.width) > mm(1) or abs(bsz.height - sz.height) > mm(1):
            print("  !! Page sizes differ by more than 1 mm. The strip will be "
                  "scaled %.4fx;" % (bsz.width / sz.width))
            print("     check the ghost PDF from `compare` before committing.")

        content, covered = [], []
        for i in range(min(base.page_count, scan.page_count)):
            b = bottom_bands(base[i], rec, a.dpi, a.white, False, a.edge)
            if b:
                content.append((i + 1, max(x["top"] for x in b)))
            zone = pymupdf.Rect(base[i].rect.x0, base[i].rect.y1 - mm(rec),
                                base[i].rect.x1, base[i].rect.y1)
            for lk in base[i].get_links():
                if pymupdf.Rect(lk["from"]).intersects(zone):
                    covered.append((i + 1, link_target(lk)))
        print("  Content in the bottom %d mm of the base: " % rec +
              (", ".join("p%d (to %.1fmm)" % (p, h) for p, h in content[:10])
               or "none - as expected"))
        if covered:
            print("  !! %d link(s) sit inside a %d mm strip. They would still "
                  "work but be invisible." % (len(covered), rec))
            for pno, tgt in covered[:10]:
                print("       p%d: %s" % (pno, tgt))
            print("  -> lower --height, or use --left/--right to lift only the")
            print("     column the handwriting is in.")
        else:
            print("  No link annotations fall inside a %d mm strip. Good." % rec)
    return 0

# ---------------------------------------------------------------------------
# ruler
# ---------------------------------------------------------------------------
def cmd_ruler(a):
    doc = open_pdf(a.scan, "scan")
    warn_rotation(doc, "scan")
    for i in parse_pages(a.pages, doc.page_count):
        p = doc[i]
        r = p.rect
        for v in range(0, int(a.search) + 1, 5):
            y = r.y1 - mm(v)
            p.draw_line((r.x0, y), (r.x1, y), color=(1, 0, 0),
                        width=0.8 if v % 10 == 0 else 0.4)
            p.insert_text((r.x0 + 6, y - 2), str(v), fontsize=6, color=(1, 0, 0))
        if a.height:
            x0 = mm(a.left) if a.left is not None else r.x0
            x1 = mm(a.right) if a.right is not None else r.x1
            p.draw_rect(pymupdf.Rect(x0, r.y1 - mm(a.height), x1, r.y1),
                        color=(0, 0.6, 0), width=1.2)
    out = resolve_out(
        a.out, a.out_dir, a.scan,
        os.path.splitext(os.path.basename(a.scan))[0] + "_RULER.pdf")
    doc.save(out, garbage=3, deflate=True)
    print("Wrote " + out)
    print("Open it in Acrobat, zoom the foot of a few pages, read off the mm.")
    print("Green box = the strip that --height would lift.")
    return 0


# ---------------------------------------------------------------------------
# compare  (red ghost overlay: pagination + alignment in one look)
# ---------------------------------------------------------------------------
def cmd_compare(a):
    base = open_pdf(a.base, "base")
    scan = open_pdf(a.scan, "scan")
    warn_rotation(base, "base")
    warn_rotation(scan, "scan")

    print(BANNER)
    print("base %s: %d pages" % (os.path.basename(a.base), base.page_count))
    print("scan %s: %d pages" % (os.path.basename(a.scan), scan.page_count))
    if base.page_count != scan.page_count:
        print("!! PAGE COUNTS DIFFER. Hyperlinking the docx may have moved the "
              "page breaks. Resolve this before stamping.")
    else:
        print("Page counts match. Now check the page BREAKS in the ghost PDF -")
        print("matching counts does not prove matching pagination.")
    print(BANNER)

    a.out = resolve_out(a.out, a.out_dir, a.base, "GHOST.pdf")
    out = pymupdf.open()
    lut = alpha_lut(a.white, a.black)
    n = max(base.page_count, scan.page_count)
    for i in parse_pages(a.pages, n):
        bp = base[i] if i < base.page_count else None
        sp = scan[i] if i < scan.page_count else None
        rect = (bp or sp).rect
        np_ = out.new_page(width=rect.width, height=rect.height)
        if bp:
            np_.show_pdf_page(np_.rect, base, i)
        if sp:
            gray = render_gray(sp, a.dpi)
            w = max(1, int(rect.width / 72.0 * a.dpi))
            h = max(1, int(rect.height / 72.0 * a.dpi))
            if gray.size != (w, h):
                gray = gray.resize((w, h))
            alpha = gray.point(lut)
            if a.opacity < 100:
                alpha = alpha.point(
                    lambda v, o=a.opacity: int(v * o / 100.0))
            red = Image.new("RGB", gray.size, (200, 0, 0)).convert("RGBA")
            red.putalpha(alpha)
            buf = io.BytesIO()
            red.save(buf, format="PNG", optimize=True)
            np_.insert_image(np_.rect, stream=buf.getvalue(),
                             overlay=True, keep_proportion=False)
        bits = ["p%d" % (i + 1)]
        bits.append("BLACK = hyperlinked" if bp else "(NO BASE PAGE)")
        bits.append("RED = scan" if sp else "(NO SCAN PAGE)")
        np_.insert_text((mm(10), mm(6)), "   ".join(bits),
                        fontsize=8, color=(0, 0, 0.8))
    out.save(a.out, garbage=3, deflate=True)
    print("")
    print("Wrote " + a.out)
    print("Flip through it. Red text sitting exactly on black = aligned and")
    print("paginated identically. Red consistently below black = a vertical")
    print("shift (correct it with --dy). Two entirely different pages")
    print("superimposed = a page break has moved. Stop and fix the docx.")
    return 0


# ---------------------------------------------------------------------------
# audit  (ink on the scan that the hyperlinked PDF does not contain)
# ---------------------------------------------------------------------------
def cmd_audit(a):
    base = open_pdf(a.base, "base")
    scan = open_pdf(a.scan, "scan")
    warn_rotation(base, "base")
    warn_rotation(scan, "scan")

    print(BANNER)
    print("AUDIT - marks on the scan that the Word export does not contain.")
    print("Anything reported ABOVE %.0f mm would be missed by a footer strip"
          % a.band)
    print("of that height. Skew and scan noise can cause false positives;")
    print("check anything reported against the ghost PDF from `compare`.")
    print(BANNER)

    n = min(base.page_count, scan.page_count)
    above = []
    for i in parse_pages(a.pages, n):
        bp, sp = base[i], scan[i]
        w = max(1, int(bp.rect.width / 72.0 * a.dpi))
        h = max(1, int(bp.rect.height / 72.0 * a.dpi))
        page_h_mm = pt(bp.rect.height)
        ppm_x, ppm_y = w / pt(bp.rect.width), h / page_h_mm

        bm = to_mask(render_gray(bp, a.dpi), a.white, False)
        sm = to_mask(render_gray(sp, a.dpi), a.white, not a.no_despeckle)
        if bm.size != (w, h):
            bm = bm.resize((w, h))
        if sm.size != (w, h):
            sm = sm.resize((w, h))

        k = min(15, int(round(a.tolerance * ppm_x)) * 2 + 1)
        grown = bm.filter(ImageFilter.MaxFilter(k))
        residual = ImageChops.subtract(sm, grown)
        residual = residual.filter(ImageFilter.MedianFilter(3))
        residual = blank_edges(residual, ppm_x, ppm_y, a.edge, sides="lrbt")

        band_y = page_h_mm - a.band                  # mm down from the top
        hits = [c for c in clusters(residual, ppm_x, ppm_y, a.cell, a.min_cells)
                if c[1] < band_y]
        if hits:
            above.append((i + 1, len(hits)))
            print("  p%3d: %d mark(s) above the strip:" % (i + 1, len(hits)))
            for c in hits[:6]:
                print("          x %.0f-%.0f mm,  %.0f-%.0f mm up from the foot"
                      % (c[0], c[2], page_h_mm - c[3], page_h_mm - c[1]))
        elif a.verbose:
            print("  p%3d: clear" % (i + 1))

    print(BANNER)
    if above:
        print("!! %d mark(s) on %d page(s) sit ABOVE the %.0f mm strip."
              % (sum(c for _, c in above), len(above), a.band))
        print("   Look at those pages in the scan. If they are initialled")
        print("   amendments, raise --height or handle them separately -")
        print("   losing them makes the composite differ from what was signed.")
        return 1
    print("No added marks above %.0f mm. A strip of that height captures"
          % a.band)
    print("everything the scan has that the Word export does not.")
    return 0


# ---------------------------------------------------------------------------
# stamp
# ---------------------------------------------------------------------------
STAMP_TAG = "AffStamp/" + VERSION


def replace_final_page(base, scan, scan_pno=None):
    """Swap base's last page for the scan's, kept at the base page size.

    Returns the link annotations that went with the discarded page.
    """
    i = base.page_count - 1
    j = scan.page_count - 1 if scan_pno is None else scan_pno
    rect = pymupdf.Rect(base[i].rect)
    lost = [(link_target(lk), tuple(round(v, 1) for v in lk["from"]))
            for lk in base[i].get_links()]
    base.delete_page(i)
    page = base.new_page(pno=i, width=rect.width, height=rect.height)
    page.show_pdf_page(page.rect, scan, j)
    return lost


def cmd_stamp(a):
    a.out = resolve_out(
        a.out, a.out_dir, a.base,
        os.path.splitext(os.path.basename(a.base))[0] + "_SIGNED.pdf")
    for name, val in (("--out", a.out),):
        if os.path.abspath(val) in (os.path.abspath(a.base),
                                    os.path.abspath(a.scan)):
            raise SystemExit("ERROR: %s must differ from --base and --scan"
                             % name)

    src = open_pdf(a.base, "base")
    warn_rotation(src, "base")
    warn_signed(src, "base")
    prev = (src.metadata or {}).get("keywords") or ""
    if "AffStamp/" in prev and not a.force:
        src.close()
        raise SystemExit("ERROR: --base has already been stamped (%s). "
                         "Stamping again would double the ink. Use --force "
                         "to override." % prev.strip())
    before = link_fingerprint(src)
    base_pages = src.page_count
    src.close()

    scan = open_pdf(a.scan, "scan")
    warn_rotation(scan, "scan")
    pmap = load_offsets(a.offsets)
    if scan.page_count != base_pages and not pmap:
        print("!! base has %d pages, scan has %d." % (base_pages, scan.page_count))
        print("   Supply --offsets with a scan_page column, or --pages, "
              "to map them explicitly.")
        if not a.pages:
            return 2

    # Work on a byte-for-byte copy so the save can be incremental: the
    # original objects are never rewritten, only appended to.
    shutil.copyfile(a.base, a.out)
    base = pymupdf.open(a.out)

    print("Link annotations in the base PDF: %d" % len(before))
    lut = alpha_lut(a.white, a.black)
    ink_rgb = parse_colour(a.ink)
    todo = parse_pages(a.pages, base.page_count)
    last = base.page_count - 1
    if (a.skip_last or a.replace_last) and last in todo:
        todo.remove(last)
        print("Not stamping the final page (%s)."
              % ("--replace-last" if a.replace_last else "--skip-last"))

    results, stamped = [], 0
    for i in todo:
        o = pmap.get(i + 1, {})
        sp_no = int(o.get("scan_page") or (i + 1)) - 1
        if not (0 <= sp_no < scan.page_count):
            print("  p%-3d SKIPPED - no scan page %d" % (i + 1, sp_no + 1))
            results.append({"page": i + 1, "status": "no-scan-page"})
            continue
        bp, sp = base[i], scan[sp_no]
        height = float(o.get("height") or a.height)

        sx0 = mm(a.left) if a.left is not None else sp.rect.x0
        sx1 = mm(a.right) if a.right is not None else sp.rect.x1
        clip = pymupdf.Rect(sx0, sp.rect.y1 - mm(height), sx1, sp.rect.y1)
        clip = clip & sp.rect                      # never exceed the page
        if clip.is_empty:
            print("  p%-3d SKIPPED - clip box is empty; check --left/--right"
                  % (i + 1))
            results.append({"page": i + 1, "status": "empty-clip"})
            continue

        gray = render_gray(sp, a.dpi, clip)
        if not a.no_despeckle:
            gray = gray.filter(ImageFilter.MedianFilter(3))
        alpha = gray.point(lut)

        if a.edge > 0:
            # Only trim sides that really are the page edge - if --left/--right
            # were given, the other edges cut through the middle of the page.
            sides = ""
            if abs(clip.x0 - sp.rect.x0) < 0.5:
                sides += "l"
            if abs(clip.x1 - sp.rect.x1) < 0.5:
                sides += "r"
            if abs(clip.y1 - sp.rect.y1) < 0.5:
                sides += "b"
            alpha = blank_edges(alpha, alpha.size[0] / pt(clip.width),
                                alpha.size[1] / pt(clip.height), a.edge, sides)

        px = alpha.point(lambda v: 255 if v >= 32 else 0)
        ink_px = sum(row_counts(px))
        area = ink_px / ((alpha.size[0] / pt(clip.width)) *
                         (alpha.size[1] / pt(clip.height)))   # mm^2

        if ink_px == 0:
            print("  p%-3d NO INK in the strip - page not initialled, or "
                  "--height/--white wrong" % (i + 1))
            results.append({"page": i + 1, "scan_page": sp_no + 1,
                            "status": "blank", "ink_mm2": 0.0})
            continue

        rgba = Image.new("RGB", alpha.size, ink_rgb).convert("RGBA")
        rgba.putalpha(alpha)
        buf = io.BytesIO()
        rgba.save(buf, format="PNG", optimize=True)

        sf = 1.0 if a.no_scale else (bp.rect.width / sp.rect.width)
        dx = mm(a.dx + float(o.get("dx_mm") or 0))
        dy = mm(a.dy + float(o.get("dy_mm") or 0))
        w, h = clip.width * sf, clip.height * sf
        x0 = bp.rect.x0 + (clip.x0 - sp.rect.x0) * sf + dx
        y1 = bp.rect.y1 + dy
        dst = pymupdf.Rect(x0, y1 - h, x0 + w, y1)

        bp.insert_image(dst, stream=buf.getvalue(), overlay=True,
                        keep_proportion=False)
        stamped += 1
        results.append({"page": i + 1, "scan_page": sp_no + 1,
                        "status": "ok", "ink_mm2": round(area, 1),
                        "height_mm": height,
                        "dest_mm": [round(pt(v), 2) for v in
                                    (dst.x0, dst.y0, dst.x1, dst.y1)]})
        print("  p%-3d <- scan p%-3d  %.0f x %.0f mm  ink %.0f mm2"
              % (i + 1, sp_no + 1, pt(w), pt(h), area))

    lost_links = []
    if a.replace_last:
        sp_no = None
        o = pmap.get(base.page_count, {})
        if o.get("scan_page"):
            sp_no = int(o["scan_page"]) - 1
        lost_links = replace_final_page(base, scan, sp_no)
        print("  p%-3d REPLACED wholesale with the scan's final page"
              % base.page_count)
        if lost_links:
            print("       !! %d link(s) went with the old page:"
                  % len(lost_links))
            for tgt, _ in lost_links:
                print("            %s" % tgt)

    meta = dict(base.metadata or {})
    meta["keywords"] = (prev + " " if prev else "") + STAMP_TAG
    base.set_metadata(meta)

    if a.full_save:
        base.save(a.out + ".tmp", garbage=3, deflate=True)
        base.close()
        os.replace(a.out + ".tmp", a.out)
    else:
        if not base.can_save_incrementally():
            print("!! Incremental save unavailable for this file; falling back "
                  "to a full save.")
            base.save(a.out + ".tmp", garbage=3, deflate=True)
            base.close()
            os.replace(a.out + ".tmp", a.out)
        else:
            # deflate=True is essential: without it the appended image
            # streams are stored uncompressed (~5 MB per page).
            base.save(a.out, incremental=True, deflate=True,
                      deflate_images=True,
                      encryption=pymupdf.PDF_ENCRYPT_KEEP)
            base.close()

    # ---- verification -----------------------------------------------------
    chk = open_pdf(a.out, "output")
    after = link_fingerprint(chk)
    chk.close()

    # Links on a page you deliberately replaced are expected to be gone;
    # every other link must survive untouched.
    expected = [x for x in before
                if not (a.replace_last and x[0] == base_pages - 1)]
    links_ok = [x[:4] for x in expected] == [x[:4] for x in after]

    ok_pages = [r for r in results if r["status"] == "ok"]
    blanks = [r["page"] for r in results if r["status"] != "ok"]
    areas = sorted(r["ink_mm2"] for r in ok_pages)
    median = areas[len(areas) // 2] if areas else 0.0
    odd = [r["page"] for r in ok_pages
           if median and (r["ink_mm2"] < median * 0.3 or
                          r["ink_mm2"] > median * 2.5)]

    manifest = {
        "tool": STAMP_TAG,
        "run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operator": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "host": os.environ.get("COMPUTERNAME") or "",
        "inputs": {
            "base": {"path": os.path.abspath(a.base), "sha256": sha256(a.base),
                     "pages": base_pages},
            "scan": {"path": os.path.abspath(a.scan), "sha256": sha256(a.scan),
                     "pages": scan.page_count},
        },
        "output": {"path": os.path.abspath(a.out), "sha256": sha256(a.out),
                   "save_mode": "full" if a.full_save else "incremental"},
        "parameters": {k: v for k, v in sorted(vars(a).items())
                       if k not in ("func", "cmd")},
        "links": {"before": len(before), "after": len(after),
                  "expected_after": len(expected), "preserved": links_ok,
                  "removed_with_final_page": [t for t, _ in lost_links]},
        "final_page_replaced": bool(a.replace_last),
        "pages": results,
        "blank_pages": blanks,
        "anomalous_pages": odd,
    }
    mpath = os.path.splitext(a.out)[0] + "_manifest.json"
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    lpath = os.path.splitext(a.out)[0] + "_links.txt"
    with open(lpath, "w", encoding="utf-8") as f:
        for pno, kind, tgt, dpage, rect in after:
            f.write("p%d\t%s\t%s\t%s\t%s\n" % (pno + 1, kind, tgt, dpage, rect))

    print(BANNER)
    print("Stamped %d of %d page(s)." % (stamped, len(todo)))
    rc = 0
    if links_ok:
        if lost_links:
            print("OK  - all %d remaining link annotations preserved."
                  % len(after))
            print("      %d link(s) were removed with the replaced final page."
                  % len(lost_links))
        else:
            print("OK  - all %d link annotations preserved." % len(after))
    else:
        print("!! LINK MISMATCH: expected %d, found %d. DO NOT USE THIS FILE."
              % (len(expected), len(after)))
        rc = 3
    if blanks:
        print("!! No signature ink found on page(s): %s"
              % ", ".join(str(p) for p in blanks))
        print("   Every page should carry both marks. Check the scan before")
        print("   you rely on this file.")
        rc = rc or 4
    if odd:
        print("?  Unusual amount of ink on page(s): %s  (median %.0f mm2)"
              % (", ".join(str(p) for p in odd), median))
        print("   Probably fine - hand-placed marks vary - but worth a look.")
    print("")
    print("Wrote %s" % a.out)
    print("      %s" % mpath)
    print("      %s" % lpath)
    print(BANNER)
    if a.replace_last:
        print("NEXT: open the output in Acrobat, check a few pages and the")
        print("final page, then certify LAST.")
        if lost_links:
            print("      Re-add the final page's links by hand first if you")
            print("      still need them.")
    else:
        print("NEXT: open the output in Acrobat and check a few pages, then")
        print("replace the final page, re-run `links --check`, certify LAST.")
    print("Never run Save As Other > Optimized PDF / Reduce File Size on it.")
    return rc


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
def cmd_selftest(a):
    import tempfile
    d = tempfile.mkdtemp(prefix="affstamp_")
    base_p = os.path.join(d, "base.pdf")
    scan_p = os.path.join(d, "scan.pdf")
    out_p = os.path.join(d, "out.pdf")
    print("Scratch folder: " + d)

    # A base PDF: three pages, body text, one file link each, empty footer.
    base = pymupdf.open()
    for i in range(3):
        p = base.new_page(width=mm(210), height=mm(297))
        p.insert_text((mm(20), mm(40)), "Affidavit page %d" % (i + 1),
                      fontsize=14)
        p.insert_text((mm(20), mm(60)), "See document DOC-00%d" % (i + 1),
                      fontsize=11)
        r = pymupdf.Rect(mm(20), mm(55), mm(90), mm(63))
        p.insert_link({"kind": pymupdf.LINK_GOTOR, "from": r,
                       "file": "./Exhibits/DOC-00%d.pdf" % (i + 1),
                       "page": 0, "to": pymupdf.Point(0, 0)})
    base.save(base_p)
    base.close()

    # A "scan": same body, plus two hand marks at the foot, on grey paper.
    scan = pymupdf.open()
    for i in range(3):
        p = scan.new_page(width=mm(209.5), height=mm(296.5))   # slightly off
        p.draw_rect(p.rect, color=None, fill=(0.93, 0.93, 0.92))
        p.insert_text((mm(20), mm(40)), "Affidavit page %d" % (i + 1),
                      fontsize=14, color=(0.25, 0.25, 0.25))
        p.insert_text((mm(20), mm(60)), "See document DOC-00%d" % (i + 1),
                      fontsize=11, color=(0.25, 0.25, 0.25))
        for x in (mm(35), mm(140)):                 # swearer, witness
            y = p.rect.y1 - mm(14)
            p.draw_polyline([(x, y), (x + mm(8), y - mm(6)),
                             (x + mm(16), y + mm(3)), (x + mm(26), y - mm(5)),
                             (x + mm(34), y)], color=(0.05, 0.05, 0.15),
                            width=1.6)
    scan.save(scan_p)
    scan.close()

    ap = build_parser().parse_args(
        ["stamp", "--base", base_p, "--scan", scan_p, "--out", out_p,
         "--height", "24", "--dpi", "300"])
    rc = cmd_stamp(ap)

    print(BANNER)
    problems = []
    if rc != 0:
        problems.append("stamp returned %d" % rc)
    src, dst = pymupdf.open(base_p), pymupdf.open(out_p)
    if link_fingerprint(src) != link_fingerprint(dst):
        problems.append("link annotations changed")
    for i in range(3):
        foot = pymupdf.Rect(0, dst[i].rect.y1 - mm(24),
                            dst[i].rect.x1, dst[i].rect.y1)
        if not dst[i].get_images():
            problems.append("page %d has no stamped image" % (i + 1))
        g = to_mask(render_gray(dst[i], 150, foot), DEF_WHITE, False)
        if sum(row_counts(g)) < 50:
            problems.append("page %d footer has no ink" % (i + 1))
    # the original bytes must still be a prefix of the incremental output
    with open(base_p, "rb") as f1, open(out_p, "rb") as f2:
        head = f1.read()
        if not f2.read(len(head)) == head:
            problems.append("output is not an incremental extension of the base")
    src.close()
    dst.close()

    if problems:
        print("SELFTEST FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("SELFTEST PASSED - links preserved, ink stamped, base bytes intact.")
    if not a.keep:
        shutil.rmtree(d, ignore_errors=True)
    else:
        print("Files kept in " + d)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_gui(a):
    try:
        import affstamp_gui
    except ImportError as e:
        print("The windowed interface needs tkinter, which is not available "
              "in this build: %s" % e)
        print("Use the menu instead - run this program with no arguments.")
        return 2
    return affstamp_gui.main()


def build_parser():
    ap = argparse.ArgumentParser(
        prog="affstamp",
        description="Overlay wet-ink signatures from a scan onto a "
                    "hyperlinked PDF without disturbing link annotations.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--version", action="version", version=STAMP_TAG)
    sub = ap.add_subparsers(dest="cmd")

    def ink(p, dpi=DEF_DPI, edge=DEF_EDGE):
        p.add_argument("--dpi", type=int, default=dpi,
                       help="analysis/render resolution")
        p.add_argument("--white", type=int, default=DEF_WHITE,
                       help="grey level at or above which a pixel is paper "
                            "(raise to 220 for faint ink, lower to 190 for a "
                            "grubby scan)")
        p.add_argument("--black", type=int, default=DEF_BLACK,
                       help="grey level at or below which ink is fully opaque")
        p.add_argument("--no-despeckle", action="store_true")
        p.add_argument("--edge", type=float, default=edge,
                       help="mm of the scan's outer edge to discard "
                            "(scanner shadow). For `stamp` this erases that "
                            "border from the stamped ink.")
        p.add_argument("--pages", help="e.g. 1-3,7,12  (default: all)")

    # links -----------------------------------------------------------------
    p = sub.add_parser("links", help="dump, repair and check link targets")
    p.add_argument("--base", required=True)
    p.add_argument("--dump", action="store_true", help="list every link")
    p.add_argument("--fix-relative", action="store_true",
                   help="rewrite absolute paths as relative ones")
    p.add_argument("--bundle-root",
                   help="folder the relative paths are measured from "
                        "(default: the folder holding --base)")
    p.add_argument("--allow-parent", action="store_true",
                   help="permit ../ in rewritten paths instead of "
                        "falling back to the bare filename")
    p.add_argument("--check", action="store_true",
                   help="verify every file target actually exists")
    p.add_argument("--out", help="output PDF for --fix-relative")
    p.set_defaults(func=cmd_links)

    # compare ---------------------------------------------------------------
    p = sub.add_parser("compare",
                       help="red ghost overlay: pagination + alignment check")
    p.add_argument("--base", required=True)
    p.add_argument("--scan", required=True)
    p.add_argument("--out", help="default: GHOST.pdf beside the base PDF")
    p.add_argument("--out-dir", help="folder for the output "
                                     "(default: the folder holding --base)")
    p.add_argument("--opacity", type=int, default=45,
                   help="percent opacity of the red scan layer")
    ink(p, dpi=150)
    p.set_defaults(func=cmd_compare)

    # audit -----------------------------------------------------------------
    p = sub.add_parser("audit",
                       help="find marks on the scan that the base lacks")
    p.add_argument("--base", required=True)
    p.add_argument("--scan", required=True)
    p.add_argument("--band", type=float, default=30.0,
                   help="mm of footer treated as the expected signature zone")
    p.add_argument("--tolerance", type=float, default=0.8,
                   help="mm of registration slack before base text counts as "
                        "a difference (raise if the scan is skewed)")
    p.add_argument("--cell", type=float, default=2.0,
                   help="mm grid used to group marks")
    p.add_argument("--min-cells", type=int, default=3,
                   help="smallest mark reported, in grid cells")
    p.add_argument("--verbose", action="store_true")
    ink(p)
    p.set_defaults(func=cmd_audit)

    # measure ---------------------------------------------------------------
    p = sub.add_parser("measure", help="find the strip height to use")
    p.add_argument("--scan", required=True)
    p.add_argument("--base", help="also check the base for content/links "
                                  "inside the strip zone")
    p.add_argument("--search", type=float, default=DEF_SEARCH,
                   help="mm of page foot to analyse")
    p.add_argument("--margin", type=float, default=4.0,
                   help="mm of clearance added above the tallest ink")
    ink(p)
    p.set_defaults(func=cmd_measure)

    # ruler -----------------------------------------------------------------
    p = sub.add_parser("ruler", help="write a scan copy with a mm grid")
    p.add_argument("--scan", required=True)
    p.add_argument("--out", help="default: <scan>_RULER.pdf")
    p.add_argument("--out-dir", help="folder for the output "
                                     "(default: the folder holding --scan)")
    p.add_argument("--height", type=float, help="draw the proposed strip")
    p.add_argument("--left", type=float)
    p.add_argument("--right", type=float)
    p.add_argument("--search", type=float, default=DEF_SEARCH)
    ink(p)
    p.set_defaults(func=cmd_ruler)

    # stamp -----------------------------------------------------------------
    p = sub.add_parser("stamp", help="do the overlay")
    p.add_argument("--base", required=True, help="the hyperlinked PDF")
    p.add_argument("--scan", required=True, help="the scanned signed PDF")
    p.add_argument("--out", help="default: <base>_SIGNED.pdf beside --base")
    p.add_argument("--out-dir", help="folder for the output "
                                     "(default: the folder holding --base)")
    p.add_argument("--height", type=float, required=True,
                   help="mm lifted from the foot of each scan page")
    p.add_argument("--left", type=float,
                   help="mm from the left edge to start (default: page edge)")
    p.add_argument("--right", type=float,
                   help="mm from the left edge to stop (default: page edge)")
    p.add_argument("--ink", default="black",
                   help="'black' or a hex colour such as 1a3a8f")
    p.add_argument("--dx", type=float, default=0.0,
                   help="mm to shift the strip; POSITIVE = right")
    p.add_argument("--dy", type=float, default=0.0,
                   help="mm to shift the strip; POSITIVE = DOWN")
    p.add_argument("--no-scale", action="store_true",
                   help="do not rescale when scan and base page widths differ")
    p.add_argument("--offsets",
                   help="CSV: page,dx_mm,dy_mm,scan_page,height")
    p.add_argument("--skip-last", action="store_true",
                   help="leave the final page alone (you will replace it "
                        "by hand in Acrobat)")
    p.add_argument("--replace-last", action="store_true",
                   help="replace the final page wholesale with the scan's "
                        "final page, at the base page size. Any link "
                        "annotations on that page go with it - they are "
                        "listed so you can re-add them.")
    p.add_argument("--full-save", action="store_true",
                   help="rewrite the whole file instead of appending")
    p.add_argument("--force", action="store_true",
                   help="stamp even if the base looks already stamped")
    ink(p, dpi=DEF_STAMP_DPI, edge=0.0)
    p.set_defaults(func=cmd_stamp)

    # gui ---------------------------------------------------------------
    p = sub.add_parser("gui", help="open the windowed interface")
    p.set_defaults(func=cmd_gui)

    # selftest --------------------------------------------------------------
    p = sub.add_parser("selftest", help="prove the tool works on this machine")
    p.add_argument("--keep", action="store_true")
    p.set_defaults(func=cmd_selftest)
    return ap


def _run(args):
    """Parse and dispatch one command line, as if typed."""
    ns = build_parser().parse_args(args)
    return ns.func(ns)


def _ask(text, default=""):
    shown = " [%s]" % default if default else ""
    try:
        s = input("%s%s: " % (text, shown))
    except EOFError:
        return default
    return s.strip().strip('"').strip("'") or default


def _yes(text, default="y"):
    return _ask(text + " y/n", default).lower().startswith("y")


def _short(path, width=44):
    if not path:
        return "(not set)"
    base = os.path.basename(path)
    return base if len(base) <= width else "..." + base[-(width - 3):]


def _set_files(st):
    """Ask for the two PDFs once; every step reuses them."""
    print("")
    print("Paste a full path, or drag the file from Explorer into this")
    print("window and press Enter. Blank keeps the current value.")
    b = _ask("Hyperlinked PDF", st.get("base", ""))
    if b and not os.path.isfile(b):
        print("!! Not found: %s" % b)
        return
    sc = _ask("Scan PDF", st.get("scan", ""))
    if sc and not os.path.isfile(sc):
        print("!! Not found: %s" % sc)
        return
    st["base"], st["scan"] = b, sc
    st["out_dir"] = os.path.dirname(os.path.abspath(b)) if b else ""
    # A different job - drop the measurements from the last one.
    for k in ("height", "edge", "dx", "dy"):
        st.pop(k, None)
    save_state(st)
    print("")
    print("Saved. Outputs will be written to: %s" % (st["out_dir"] or "."))


def _need(st, *keys):
    missing = [k for k in keys if not st.get(k)]
    if missing:
        print("")
        print("!! Choose option 1 first and set the %s."
              % " and ".join("hyperlinked PDF" if m == "base" else "scan PDF"
                             for m in missing))
        return False
    for k in keys:
        if not os.path.isfile(st[k]):
            print("")
            print("!! No longer on disk: %s" % st[k])
            print("   Choose option 1 and set it again.")
            return False
    return True


def _common(st):
    """The file arguments every step shares."""
    return ["--base", st["base"], "--scan", st["scan"],
            "--out-dir", st.get("out_dir") or "."]


def _stamp_args(st, pages=None, replace_last=False):
    args = ["stamp"] + _common(st) + ["--height", str(st["height"])]
    if st.get("edge"):
        args += ["--edge", str(st["edge"])]
    for k in ("dx", "dy"):
        if st.get(k):
            args += ["--" + k, str(st[k])]
    if pages:
        args += ["--pages", pages]
    args += ["--replace-last"] if replace_last else ["--skip-last"]
    return args


def _header(st):
    print("")
    print(BANNER)
    print(" AffStamp/%s" % VERSION)
    print(BANNER)
    print("   Hyperlinked PDF   %s" % _short(st.get("base")))
    print("   Scan PDF          %s" % _short(st.get("scan")))
    print("   Output folder     %s" % (st.get("out_dir") or "(not set)"))
    if st.get("height"):
        extra = "".join(" --%s %s" % (k, st[k])
                        for k in ("edge", "dx", "dy") if st.get(k))
        print("   Measured          --height %s%s" % (st["height"], extra))
    print("-" * 72)
    print("   1) Set the two PDFs")
    print("   2) Check and repair the hyperlinks")
    print("   3) Ghost overlay   - pagination and alignment")
    print("   4) Measure         - work out the strip height")
    print("   5) Ruler PDF       - eyeball the strip on a mm grid")
    print("   6) Stamp           - TRIAL run on a few pages")
    print("   7) Stamp           - FULL run")
    print("")
    print("   8) Audit for marks above the strip     (optional)")
    print("   9) Self-test")
    print("   0) Quit")


def interactive():
    st = load_state()
    if st.get("base") or st.get("scan"):
        print("")
        print("Picking up where you left off. Option 1 changes the files.")

    while True:
        _header(st)
        c = _ask("Choice")
        try:
            if c == "1":
                _set_files(st)

            elif c == "2":
                if not _need(st, "base"):
                    continue
                _run(["links", "--base", st["base"], "--dump", "--check"])
                if _yes("\nRepair absolute paths into relative ones?", "n"):
                    out = os.path.join(
                        st["out_dir"],
                        os.path.splitext(os.path.basename(st["base"]))[0]
                        + "_fixed.pdf")
                    out = _ask("Write repaired PDF to", out)
                    if _run(["links", "--base", st["base"], "--fix-relative",
                             "--out", out, "--check"]) == 0:
                        st["base"] = out
                        save_state(st)
                        print("\nNow using %s as the hyperlinked PDF."
                              % os.path.basename(out))

            elif c == "3":
                if not _need(st, "base", "scan"):
                    continue
                _run(["compare"] + _common(st))

            elif c == "4":
                if not _need(st, "base", "scan"):
                    continue
                LAST_SUGGESTED.clear()
                _run(["measure", "--base", st["base"], "--scan", st["scan"]])
                if LAST_SUGGESTED.get("height"):
                    st["height"] = LAST_SUGGESTED["height"]
                    if LAST_SUGGESTED.get("edge"):
                        st["edge"] = LAST_SUGGESTED["edge"]
                    save_state(st)
                    print("")
                    print("Remembered for the stamp steps: --height %s%s"
                          % (st["height"],
                             " --edge %s" % st["edge"] if st.get("edge") else ""))
                    if not _yes("Keep that height?", "y"):
                        h = _ask("Strip height mm", str(st["height"]))
                        try:
                            st["height"] = float(h)
                            save_state(st)
                        except ValueError:
                            print("!! Not a number; keeping %s" % st["height"])

            elif c == "5":
                if not _need(st, "scan"):
                    continue
                args = ["ruler", "--scan", st["scan"],
                        "--out-dir", st.get("out_dir") or "."]
                h = _ask("Strip height mm to draw",
                         str(st.get("height", "")) if st.get("height") else "")
                if h:
                    args += ["--height", h]
                pg = _ask("Pages (blank = all)", "1-3")
                if pg:
                    args += ["--pages", pg]
                _run(args)

            elif c in ("6", "7"):
                if not _need(st, "base", "scan"):
                    continue
                if not st.get("height"):
                    h = _ask("Strip height mm (run option 4 if unsure)")
                    if not h:
                        continue
                    try:
                        st["height"] = float(h)
                    except ValueError:
                        print("!! Not a number.")
                        continue
                    save_state(st)

                if c == "6":
                    pages = _ask("Pages for the trial", "1-3")
                    out = os.path.join(st["out_dir"], "TEST.pdf")
                    rc = _run(_stamp_args(st, pages=pages) + ["--out", out])
                    print("")
                    print("Print a page of TEST.pdf and hold it against the")
                    print("original. If the marks sit slightly off, set a")
                    print("nudge below (+dx right, +dy down) and try again.")
                    if _yes("Set a nudge now?", "n"):
                        st["dx"] = _ask("dx mm  (+ = right)",
                                        str(st.get("dx", "0"))) or "0"
                        st["dy"] = _ask("dy mm  (+ = down)",
                                        str(st.get("dy", "0"))) or "0"
                        save_state(st)
                else:
                    print("")
                    print("The final page normally carries the full execution")
                    print("block, not just initials. AffStamp can swap in the")
                    print("scan's final page whole, instead of stamping it.")
                    print("Any hyperlinks on that page go with it - they get")
                    print("listed so you can re-add them in Acrobat.")
                    repl = _yes("Replace the final page with the scan's?", "y")
                    rc = _run(_stamp_args(st, replace_last=repl))
                    if rc == 0:
                        print("")
                        print("Done. Check it in Acrobat, then certify.")

            elif c == "8":
                if not _need(st, "base", "scan"):
                    continue
                band = _ask("Strip height to test against",
                            str(st.get("height", 30)))
                _run(["audit"] + _common(st)[:4] + ["--band", band])

            elif c == "9":
                _run(["selftest"])

            elif c in ("0", "q", "quit", "exit"):
                return
            else:
                print("!! Choose a number from the menu.")
        except SystemExit as e:
            if e.code not in (None, 0):
                print("(%s)" % e)
        except KeyboardInterrupt:
            print("\nCancelled.")
        except Exception as e:
            print("ERROR: %s: %s" % (type(e).__name__, e))

def main():
    if len(sys.argv) == 1:
        interactive()
        input("\nPress Enter to close...")
        return 0
    ap = build_parser()
    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
