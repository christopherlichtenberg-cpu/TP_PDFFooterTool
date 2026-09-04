# Conversation
- conversationId: 1a6ca6b3-6367-47e6-91dc-ef5d90ef0ace
- endpoint: anthropic
- title: Overlaying Scanned Signatures on Hyperlinked PDF
- exportAt: 15:41:57 GMT+1000 (Australian Eastern Standard Time)

## Options
- presetId: null
- model: claude-opus-5
- resendFiles: true
- thinking: false
- iconURL: anthropic
- spec: claude-opus-5
- maxContextTokens: 32000
- endpoint: anthropic
- endpointType: anthropic
- title: Overlaying Scanned Signatures on Hyperlinked PDF

## History
**User**
I have a PDF dilemma.
I have two documents - a word doc, and a PDF
The PDF is a scanned harcdopy of a signed affidavit, and the word doc is the unsigned .docx version of it.
We need to hyperlink all references to Document IDs within the docx version to a relative path and then convert this to a PDF. But then we also need all the signatures from the original scan to be overlaid to the new hyperlinked one without breaking the relative paths of the hyperlinks.
I was fine if it was just the final page that was signed - I was planning to just replace the last page of the hyperlinked word doc saved as a PDF. But our signed copy has initials at the bottom of EVERY page. The scan is also a terrible quality b/w scan of a document which clearly has colour, so we can't just convert the signed can to word.
Is there a way i can take the bottom x mm of the signed doc and put these on top of the new hyperlinked one without it being an editable shape while ALSO protecting the relative hyperlinks?


**Claude**
## The key insight that makes this easy

In a PDF, hyperlinks are **annotations** — objects attached to the page, stored completely separately from the page's **content stream** (the drawn marks). Stamping extra content onto a page doesn't touch the annotation array at all.

So: as long as you **add** the signature strip as content to the *existing* pages of your Word-generated PDF, and never re-render, re-print, or re-distill that file, the links survive untouched.

Things that **will** kill your links:
- "Print to PDF" / Microsoft Print to PDF on the final file
- Acrobat's *Flatten*, *Optimize → Discard objects → Discard links*, or *Combine Files* in some versions
- Re-OCRing the merged output
- Anything that rasterises pages

Things that **won't**:
- `page.show_pdf_page()` / `insert_image()` in PyMuPDF (adds an XObject to the content stream)
- `qpdf --overlay`
- pypdf `merge_transformed_page` (base page annotations are kept)

---

## Recommended pipeline

1. Set the **Hyperlink Base** in Word (`File → Info → Properties → Advanced Properties → Summary → Hyperlink base`) and insert your Document ID links as relative paths.
2. Export with **`File → Save As → PDF`** (Windows Word) or the Acrobat PDFMaker ribbon — *not* Print to PDF. Mac Word is notorious for mangling/absolutising links; use Windows if you can.
3. **Verify the link targets** in the exported PDF before you do anything else (script below dumps them).
4. Stamp the bottom strip of each scanned page onto the corresponding new page.
5. Lock it down.

---

## Script A — simple version (vector-ish copy of the strip)

```python
import fitz  # pip install pymupdf

BASE = "hyperlinked.pdf"     # Word export, links intact
SCAN = "signed_scan.pdf"
OUT  = "final_signed_hyperlinked.pdf"

MM   = 72 / 25.4             # points per mm
STRIP_H = 25 * MM            # bottom 25 mm — tune this

base = fitz.open(BASE)
scan = fitz.open(SCAN)
assert base.page_count == scan.page_count, "page counts differ — fix first"

for i in range(base.page_count):
    bp, sp = base[i], scan[i]
    src = fitz.Rect(0, sp.rect.height - STRIP_H, sp.rect.width,  sp.rect.height)
    dst = fitz.Rect(0, bp.rect.height - STRIP_H, bp.rect.width,  bp.rect.height)
    bp.show_pdf_page(dst, scan, i, clip=src, overlay=True)

base.save(OUT, garbage=4, deflate=True)
```

**Problem with this version:** the scanned strip has a white (or dirty grey) background, which will paint over anything in your new footer — including page numbers or footer Document IDs. The *link annotation* still works (it's clickable but invisible), which is worse than useless.

---

## Script B — transparent ink overlay (use this one)

Because your scan is B/W, thresholding the ink out is trivial and gives you a strip with a genuinely transparent background.

```python
import io
import fitz
import numpy as np
from PIL import Image

BASE, SCAN, OUT = "hyperlinked.pdf", "signed_scan.pdf", "final_signed_hyperlinked.pdf"
MM = 72 / 25.4
STRIP_H = 25 * MM     # bottom 25 mm
DPI = 400             # render resolution of the lifted strip

base, scan = fitz.open(BASE), fitz.open(SCAN)
assert base.page_count == scan.page_count

for i in range(base.page_count):
    bp, sp = base[i], scan[i]

    clip = fitz.Rect(0, sp.rect.height - STRIP_H, sp.rect.width, sp.rect.height)
    pix  = sp.get_pixmap(dpi=DPI, clip=clip, colorspace=fitz.csGRAY)
    grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)

    # soft alpha: darker pixel -> more opaque. Ink stays anti-aliased, paper vanishes.
    WHITE_CUT = 205                     # anything lighter than this is "paper"
    a = np.clip((WHITE_CUT - grey.astype(np.int16)) * (255 / WHITE_CUT), 0, 255).astype(np.uint8)
    rgb = np.zeros_like(grey)           # render ink as black; swap for ink colour if you like
    rgba = np.dstack([rgb, rgb, rgb, a])

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)

    dst = fitz.Rect(0, bp.rect.height - STRIP_H, bp.rect.width, bp.rect.height)
    bp.insert_image(dst, stream=buf.getvalue(), overlay=True)

base.save(OUT, garbage=4, deflate=True)
```

Tune `WHITE_CUT` up if the scan is dark/dirty, down if faint initials disappear. If the scan has heavy speckle, add a quick despeckle (`scipy.ndimage.median_filter(grey, 3)`) before thresholding.

**Even safer:** don't lift the full width. If the initials sit bottom-right, clip only that box, so you can't possibly cover footer text:

```python
clip = fitz.Rect(sp.rect.width - 70*MM, sp.rect.height - 20*MM, sp.rect.width, sp.rect.height)
```

---

## Verify and repair the links in the same pass

Do this *before* saving — and it also solves the "Word turned my relative link into `file:///C:/Users/...`" problem:

```python
for p in base:
    for l in p.get_links():
        print(p.number, l.get("uri") or l.get("file"), l["from"])
```

To force them relative:

```python
import re, os
for p in base:
    for l in p.get_links():
        tgt = l.get("uri") or l.get("file") or ""
        if tgt.startswith("file:///") or re.match(r"^[A-Za-z]:\\", tgt):
            l["file"] = "./" + os.path.basename(tgt.replace("\\", "/"))
            l["uri"]  = None
            p.update_link(l)
```

(Adjust to rebuild whatever relative structure your bundle uses — `../Exhibits/DOC-000123.pdf` etc.)

---

## Alignment gotchas

- **Page size mismatch.** Scans are often 209×296 mm rather than exactly A4, or the scanner added a skew. Print one merged page and lay it over the original before you commit to all 40 pages.
- **Skew.** If the scan is rotated by a degree or two, the initials will sit at a slight angle relative to the crisp text. Deskew first: `ocrmypdf --deskew --clean --redo-ocr in.pdf deskewed.pdf`, or ScanTailor, and use the deskewed file as `SCAN`. (OCR on the *scan* is fine — never on the output.)
- **Per-page nudge.** If one page is off, add an offset: `dst = dst + (0, dy)`.
- **Page count / order.** If the scan has a stray cover sheet, build an explicit mapping `{base_page: scan_page}` rather than assuming 1:1.

---

## Making it non-editable

The strip is now an image XObject baked into the page content stream — it's not an annotation, stamp, or shape, so it can't be selected, dragged, or deleted in Acrobat without full content editing. To harden further:

- **Certify the PDF** with a digital signature ("Certify (visible/invisible)" in Acrobat, or `pyHanko` / `endesive` in Python). This is the real answer to "not editable": any subsequent alteration invalidates the certification, and it's stronger evidence than a flattened image. Choose the permission level *"Form filling and annotations allowed"* or *"No changes allowed"*. Signing appends an incremental update — **it does not touch your link annotations**.
- Or set an owner password with editing disabled (weak, but stops casual tampering).

Then re-verify the links in Acrobat *and* in whatever viewer the recipient will use — relative `/Launch` and `/GoToR` file links behave differently across Acrobat, Edge, Chrome and DMS viewers, and some block them entirely unless the whole bundle sits in a trusted folder.

---

## Alternative worth considering: link the scan instead

If the scan is legible enough, the cleaner-in-principle approach is to leave the executed document untouched and *add* link annotations to it:

```bash
ocrmypdf --deskew --clean signed_scan.pdf scan_ocr.pdf
```

then find each Document ID with `page.search_for("DOC-000123")` and `page.insert_link({"kind": fitz.LINK_LAUNCH, "file": "./Exhibits/DOC-000123.pdf", "from": rect})`.

That gives you a hyperlinked version of the *actual signed instrument*, with no composite. You said the scan quality is poor, so the composite is probably the right call — but check whether whoever receives this has a view on it.

---

## One non-technical caveat

You're producing a document that is neither the executed original nor the unexecuted draft. That's normal and generally fine for an e-filed/hyperlinked bundle, but the safe framing is: **the scanned PDF remains the executed affidavit; the hyperlinked composite is a navigable working copy.** Keep the original scan in the bundle, do a page-by-page text comparison (`diff` the extracted text of the OCR'd scan vs the Word export) to prove no wording drifted between the draft and what was actually signed, and check your court/registry rules before serving the composite as the affidavit itself.


**User**
ok, I can see some python scripts there. I believe the machines that have adobe pro XI where the final steps will need to be run - after the word doc is hyperlinked and saved to PDF - don't have python installed.
So we may want to/have to package these up as an .exe
I also want some ability to find out what the x mm from the scanned copy actually is so we know what to input to "pull" from the source and "fill" onto the hyperlinked one
But yes - complete preference for the transparent background/ink overlay version


**Claude**
## What you'll end up with

One self-contained `AffStamp.exe` (no Python on the target box) with three modes:

| Mode | What it does |
|---|---|
| `measure` | Analyses the bottom of the **scan**, prints ink bands in mm from the bottom edge, suggests a strip height/width, and **also checks the hyperlinked PDF** for content or link annotations that the strip would cover |
| `ruler` | Writes a copy of the scan with red 5 mm gridlines + labels and a green proposed clip box, so you can eyeball it in Acrobat XI |
| `stamp` | Does the transparent-ink overlay and hard-verifies that every link annotation survived |

Only two dependencies (PyMuPDF + Pillow) — no numpy, which keeps the exe around 45 MB instead of 90 MB.

---

## `affstamp.py`

```python
#!/usr/bin/env python3
"""
AffStamp - overlay signature/initial strips from a scanned PDF onto a
hyperlinked PDF without touching link annotations.

  affstamp measure --scan signed.pdf [--base hyperlinked.pdf]
  affstamp ruler   --scan signed.pdf [--height 25]
  affstamp stamp   --base hyperlinked.pdf --scan signed.pdf --out final.pdf --height 25
"""

import io
import os
import sys
import csv
import argparse

import fitz                      # PyMuPDF
from PIL import Image, ImageFilter

MMPT = 72.0 / 25.4
def mm(v):   return v * MMPT     # mm -> points
def pt(v):   return v / MMPT     # points -> mm

DEF_WHITE = 205                  # lighter than this = paper
DEF_BLACK = 60                   # darker than this = solid ink
DEF_SEARCH = 60.0                # mm of page bottom to analyse
BANNER = "=" * 68


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def _strip_image(page, search_mm, dpi, white_cut, despeckle):
    r = page.rect
    clip = fitz.Rect(r.x0, r.y1 - mm(search_mm), r.x1, r.y1)
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    if despeckle:
        img = img.filter(ImageFilter.MedianFilter(3))
    bw = img.point(lambda v: 255 if v < white_cut else 0)
    return bw, clip


def analyse_page(page, search_mm=DEF_SEARCH, dpi=200, white_cut=DEF_WHITE,
                 despeckle=True, min_ink_frac=0.0008, gap_mm=2.0):
    """Return list of ink bands: (bottom_mm, top_mm, left_mm, right_mm)."""
    bw, clip = _strip_image(page, search_mm, dpi, white_cut, despeckle)
    w, h = bw.size
    ppm_y = h / search_mm
    ppm_x = w / pt(clip.width)
    min_ink = max(2, int(w * min_ink_frac))
    gap_rows = max(1, int(gap_mm * ppm_y))

    rows = [y for y in range(h)
            if bw.crop((0, y, w, y + 1)).histogram()[255] >= min_ink]
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
        bbox = bw.crop((0, top_row, w, bot_row + 1)).getbbox()
        left_mm  = bbox[0] / ppm_x if bbox else 0.0
        right_mm = bbox[2] / ppm_x if bbox else pt(clip.width)
        bands.append(((h - 1 - bot_row) / ppm_y,      # bottom, mm from foot
                      (h - 1 - top_row) / ppm_y,      # top,    mm from foot
                      left_mm, right_mm))
    bands.sort()
    return bands


def cmd_measure(a):
    scan = fitz.open(a.scan)
    warn_rotation(scan, "scan")
    pages = parse_pages(a.pages, scan.page_count)

    print(BANNER)
    print(f"SCAN: {os.path.basename(a.scan)}  ({scan.page_count} pages)")
    print("Ink found in the bottom %.0f mm (mm measured UP from the page edge):"
          % a.search)
    print(BANNER)

    overall_top = 0.0
    overall_left, overall_right = 1e9, 0.0
    for i in pages:
        bands = analyse_page(scan[i], a.search, a.dpi, a.white, not a.no_despeckle)
        if not bands:
            print(f"  p{i+1:>3}: (nothing)")
            continue
        desc = "  ".join(f"{b:.1f}-{t:.1f}mm [x {l:.0f}-{r:.0f}mm]"
                         for b, t, l, r in bands)
        print(f"  p{i+1:>3}: {desc}")
        overall_top   = max(overall_top, max(t for _, t, _, _ in bands))
        overall_left  = min(overall_left, min(l for _, _, l, _ in bands))
        overall_right = max(overall_right, max(r for _, _, _, r in bands))

    if overall_top == 0:
        print("\nNo ink detected - raise --search or lower --white.")
        return 1

    rec_h = round(overall_top + a.margin + 0.49)
    print(BANNER)
    print(f"Highest ink in the searched zone : {overall_top:.1f} mm")
    print(f"Horizontal extent of that ink    : {overall_left:.0f} - "
          f"{overall_right:.0f} mm from the left edge")
    print(f"SUGGESTED  --height {rec_h}")
    print(f"If the top band is printed footer text rather than handwriting,")
    print(f"pick the top of the handwriting band instead and add ~{a.margin:.0f} mm.")
    print(BANNER)

    if a.base:
        base = fitz.open(a.base)
        warn_rotation(base, "base")
        print(f"\nBASE: {os.path.basename(a.base)}  ({base.page_count} pages)")
        if base.page_count != scan.page_count:
            print("  !! PAGE COUNT MISMATCH - resolve before stamping.")
        clash_c, clash_l = [], []
        for i in range(min(base.page_count, len(base))):
            b = analyse_page(base[i], rec_h, a.dpi, a.white, False)
            if b:
                clash_c.append((i + 1, max(t for _, t, _, _ in b)))
            zone = fitz.Rect(base[i].rect.x0, base[i].rect.y1 - mm(rec_h),
                             base[i].rect.x1, base[i].rect.y1)
            for lk in base[i].get_links():
                if fitz.Rect(lk["from"]).intersects(zone):
                    clash_l.append((i + 1, lk.get("uri") or lk.get("file") or "?"))
        print(f"  Content inside the bottom {rec_h} mm of the base PDF: "
              + (", ".join(f"p{p} (to {h:.1f}mm)" for p, h in clash_c[:12]) or "none"))
        if clash_l:
            print(f"  !! {len(clash_l)} LINK(S) sit inside the strip zone and would be "
                  f"hidden but still clickable:")
            for p, u in clash_l[:12]:
                print(f"       p{p}: {u}")
            print("  -> reduce --height, or use --left/--right to clip only the "
                  "handwriting column.")
        else:
            print("  No link annotations fall inside the strip zone.  Good.")
    return 0


# --------------------------------------------------------------------------
# ruler
# --------------------------------------------------------------------------
def cmd_ruler(a):
    doc = fitz.open(a.scan)
    warn_rotation(doc, "scan")
    for i in parse_pages(a.pages, doc.page_count):
        p = doc[i]
        r = p.rect
        for v in range(0, int(a.search) + 1, 5):
            y = r.y1 - mm(v)
            p.draw_line((r.x0, y), (r.x1, y),
                        color=(1, 0, 0), width=0.4 if v % 10 else 0.8)
            p.insert_text((r.x0 + 6, y - 2), f"{v}", fontsize=6, color=(1, 0, 0))
        if a.height:
            x0 = mm(a.left) if a.left else r.x0
            x1 = mm(a.right) if a.right else r.x1
            p.draw_rect(fitz.Rect(x0, r.y1 - mm(a.height), x1, r.y1),
                        color=(0, 0.6, 0), width=1.2)
    out = a.out or os.path.splitext(a.scan)[0] + "_RULER.pdf"
    doc.save(out, garbage=3, deflate=True)
    print(f"Wrote {out}  - open in Acrobat and read off the mm you need.")
    return 0


# --------------------------------------------------------------------------
# stamp
# --------------------------------------------------------------------------
def link_fingerprint(doc):
    out = []
    for p in doc:
        for lk in p.get_links():
            out.append((p.number, lk.get("kind"),
                        lk.get("uri") or lk.get("file") or "",
                        tuple(round(v, 1) for v in lk["from"])))
    return out


def cmd_stamp(a):
    base = fitz.open(a.base)
    scan = fitz.open(a.scan)
    warn_rotation(base, "base"); warn_rotation(scan, "scan")

    pmap = load_offsets(a.offsets)
    n = base.page_count
    if scan.page_count != n and not pmap:
        print(f"!! base has {n} pages, scan has {scan.page_count}. "
              f"Supply --offsets with a scan_page column.")
        return 2

    before = link_fingerprint(base)
    print(f"Link annotations in base PDF: {len(before)}")

    lut = []
    span = max(1, a.white - a.black)
    for v in range(256):
        lut.append(0 if v >= a.white else
                   255 if v <= a.black else
                   int(round((a.white - v) * 255.0 / span)))

    ink_rgb = parse_colour(a.ink)
    todo = parse_pages(a.pages, n)

    for i in todo:
        o = pmap.get(i + 1, {})
        sp_no = int(o.get("scan_page", i + 1)) - 1
        if sp_no >= scan.page_count:
            print(f"  p{i+1}: no scan page {sp_no+1}, skipped"); continue
        bp, sp = base[i], scan[sp_no]

        sx0 = mm(a.left) if a.left is not None else sp.rect.x0
        sx1 = mm(a.right) if a.right is not None else sp.rect.x1
        clip = fitz.Rect(sx0, sp.rect.y1 - mm(a.height), sx1, sp.rect.y1)

        pix = sp.get_pixmap(dpi=a.dpi, clip=clip, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        if not a.no_despeckle:
            img = img.filter(ImageFilter.MedianFilter(3))
        alpha = img.point(lut)
        if alpha.getextrema()[1] == 0:
            print(f"  p{i+1}: strip is blank - check --height / --white"); continue

        rgba = Image.new("RGB", img.size, ink_rgb).convert("RGBA")
        rgba.putalpha(alpha)
        buf = io.BytesIO()
        rgba.save(buf, format="PNG", optimize=True)

        sf = (bp.rect.width / sp.rect.width) if not a.no_scale else 1.0
        dx = mm(a.dx + float(o.get("dx_mm", 0)))
        dy = mm(a.dy + float(o.get("dy_mm", 0)))
        w, h = clip.width * sf, clip.height * sf
        x0 = bp.rect.x0 + (clip.x0 - sp.rect.x0) * sf + dx
        y1 = bp.rect.y1 + dy
        dst = fitz.Rect(x0, y1 - h, x0 + w, y1)

        bp.insert_image(dst, stream=buf.getvalue(),
                        overlay=True, keep_proportion=False)
        print(f"  p{i+1}: stamped from scan p{sp_no+1}  "
              f"({w/MMPT:.0f} x {h/MMPT:.0f} mm)")

    base.save(a.out, garbage=3, deflate=True)
    base.close()

    chk = fitz.open(a.out)
    after = link_fingerprint(chk)
    rep = os.path.splitext(a.out)[0] + "_links.txt"
    with open(rep, "w", encoding="utf-8") as f:
        for pno, kind, tgt, rect in after:
            f.write(f"p{pno+1}\t{kind}\t{tgt}\t{rect}\n")
    print(BANNER)
    if [x[:3] for x in before] == [x[:3] for x in after]:
        print(f"OK - all {len(after)} link annotations preserved.")
    else:
        print(f"!! LINK MISMATCH: {len(before)} before, {len(after)} after. "
              f"DO NOT USE THIS FILE.")
        return 3
    print(f"Wrote {a.out}\nLink report: {rep}")
    print("Now: verify in Acrobat, then Certify. Never run "
          "Save As Optimized / Reduce File Size / Print to PDF on it.")
    return 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def warn_rotation(doc, label):
    rots = {p.rotation for p in doc}
    if rots - {0}:
        print(f"!! {label} has rotated pages {sorted(rots)} - normalise the "
              f"rotation first or the strip will land on the wrong edge.")


def parse_pages(spec, total):
    if not spec:
        return list(range(total))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            s, e = part.split("-")
            out += list(range(int(s) - 1, int(e)))
        elif part:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < total]


def parse_colour(s):
    if not s or s.lower() == "black":
        return (0, 0, 0)
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def load_offsets(path):
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {int(r["page"]): r for r in csv.DictReader(f)}


def build_parser():
    ap = argparse.ArgumentParser(prog="affstamp")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--pages")
        p.add_argument("--search", type=float, default=DEF_SEARCH)
        p.add_argument("--dpi", type=int, default=200)
        p.add_argument("--white", type=int, default=DEF_WHITE)
        p.add_argument("--no-despeckle", action="store_true")

    m = sub.add_parser("measure"); common(m)
    m.add_argument("--scan", required=True)
    m.add_argument("--base")
    m.add_argument("--margin", type=float, default=4.0)
    m.set_defaults(func=cmd_measure)

    r = sub.add_parser("ruler"); common(r)
    r.add_argument("--scan", required=True)
    r.add_argument("--out")
    r.add_argument("--height", type=float)
    r.add_argument("--left", type=float)
    r.add_argument("--right", type=float)
    r.set_defaults(func=cmd_ruler)

    s = sub.add_parser("stamp")
    s.add_argument("--base", required=True)
    s.add_argument("--scan", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--height", type=float, required=True)
    s.add_argument("--left", type=float)
    s.add_argument("--right", type=float)
    s.add_argument("--dpi", type=int, default=400)
    s.add_argument("--white", type=int, default=DEF_WHITE)
    s.add_argument("--black", type=int, default=DEF_BLACK)
    s.add_argument("--ink", default="black")
    s.add_argument("--dx", type=float, default=0.0)
    s.add_argument("--dy", type=float, default=0.0)
    s.add_argument("--no-scale", action="store_true")
    s.add_argument("--no-despeckle", action="store_true")
    s.add_argument("--offsets")
    s.add_argument("--pages")
    s.set_defaults(func=cmd_stamp)
    return ap


def interactive():
    print(BANNER); print("AffStamp"); print(BANNER)
    q = lambda t, d="": (input(f"{t}{' ['+d+']' if d else ''}: ").strip().strip('"') or d)
    while True:
        print("\n 1) Measure scan (and check base for clashes)\n"
              " 2) Make ruler PDF\n 3) Stamp\n 4) Quit")
        c = q("Choice")
        try:
            if c == "1":
                args = ["measure", "--scan", q("Scan PDF")]
                b = q("Hyperlinked PDF (blank to skip)")
                if b: args += ["--base", b]
            elif c == "2":
                args = ["ruler", "--scan", q("Scan PDF")]
                h = q("Proposed height mm (blank = none)")
                if h: args += ["--height", h]
            elif c == "3":
                args = ["stamp", "--base", q("Hyperlinked PDF"),
                        "--scan", q("Scan PDF"),
                        "--out",  q("Output PDF", "FINAL.pdf"),
                        "--height", q("Strip height mm")]
                l = q("Left edge mm (blank = full width)")
                if l:
                    args += ["--left", l, "--right", q("Right edge mm", "999")]
            else:
                return
            p = build_parser(); a = p.parse_args(args); a.func(a)
        except SystemExit:
            pass
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        interactive()
        input("\nPress Enter to close...")
    else:
        p = build_parser()
        a = p.parse_args()
        if not getattr(a, "func", None):
            p.print_help(); sys.exit(1)
        sys.exit(a.func(a))
```

---

## Working out the mm

**Step 1 — measure.** This is the bit you asked for:

```
AffStamp.exe measure --scan signed_scan.pdf --base hyperlinked.pdf
```

Typical output:

```
  p  1: 6.2-17.8mm [x 142-196mm]   24.1-28.6mm [x 20-190mm]
  p  2: 5.9-17.4mm [x 141-195mm]   24.0-28.6mm [x 20-190mm]
...
Highest ink in the searched zone : 28.6 mm
Horizontal extent of that ink    : 20 - 196 mm from the left edge
SUGGESTED  --height 33
```

Read that as: the initials are the **6–18 mm** band on the right-hand side (x 142–196 mm), and the **24–29 mm** band running the full width is the printed footer line, which you do **not** want to lift because your hyperlinked PDF regenerates it (and may have a link in it). So the real answer is:

```
--height 21 --left 138 --right 200
```

i.e. only the handwriting band + a few mm margin, and only the right-hand column.

The `--base` check then tells you whether anything in the hyperlinked PDF lives under that box — in particular **link annotations**, which are the dangerous case: a covered link still works but is invisible, which looks like a bug to the reader.

**Step 2 — confirm visually.**

```
AffStamp.exe ruler --scan signed_scan.pdf --height 21 --left 138 --right 200 --pages 1-3
```

Open `signed_scan_RULER.pdf` in Acrobat XI, zoom the footer, confirm the green box contains the initials and nothing else. Adjust and re-run — it's a few seconds per pass.

**Step 3 — trial stamp on 3 pages** before committing:

```
AffStamp.exe stamp --base hyperlinked.pdf --scan signed_scan.pdf ^
  --out TEST.pdf --height 21 --left 138 --right 200 --pages 1,2,40
```

Print page 1 of `TEST.pdf` and hold it against the original — this is the only reliable way to catch scanner skew and the "scan is 209×296 not 210×297" problem. If the initials sit a couple of mm off, add `--dx -1.5 --dy 0.8`. If only *some* pages are off, use `--offsets offsets.csv`:

```csv
page,dx_mm,dy_mm,scan_page
7,0,-1.2,7
8,1.0,0,9
```

**Step 4 — full run**, then read the last lines: `OK - all 128 link annotations preserved.`

**Tuning the ink extraction:** if faint initials disappear, raise `--white` (215–225). If the scan is grubby and you get grey haze, lower it (185–195) and keep despeckling on. `--black` controls where ink becomes fully solid; lower it (40) if strokes look washed out.

Leave `--ink black`. It accepts a hex colour, but recolouring a signature on an affidavit to "look like the original blue" is exactly the kind of thing you don't want to have to explain later.

---

## Packaging to .exe

On a build machine (matching the target's Windows bitness):

```bat
:: build.bat
py -3.11 -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install pymupdf pillow pyinstaller

pyinstaller --noconfirm --clean --onedir --console ^
  --name AffStamp ^
  --collect-all pymupdf ^
  --exclude-module numpy --exclude-module scipy ^
  --exclude-module matplotlib --exclude-module tkinter ^
  affstamp.py
```

Ship the whole `dist\AffStamp\` folder (zip it). Then `dist\AffStamp\AffStamp.exe`.

Notes for the Acrobat XI machines specifically:

- **Use `--onedir`, not `--onefile`.** A onefile exe unpacks itself into `%TEMP%` and runs from there; locked-down SOEs with AppLocker/software-restriction policies commonly block execution from `%TEMP%`, and onefile also adds several seconds of startup per invocation. Onedir in a normal folder sidesteps both.
- **Antivirus.** Unsigned PyInstaller binaries get flagged as `Wacatac`/`Trojan:Script/Wacapew` with depressing regularity. Get IT to allowlist the folder hash, or code-sign it, before you promise anyone a delivery date.
- **If those machines are Windows 7** (plausible alongside Acrobat XI), the current toolchain won't run. Build with Python 3.8.10 and pin: `pip install "pymupdf==1.23.8" "pillow==10.0.1" "pyinstaller==5.13.2"`. Change `--collect-all pymupdf` to `--collect-all fitz` for that PyMuPDF vintage. On 32-bit targets you must build in 32-bit Python.
- No admin rights needed — it's a folder copy, and everything runs in user space.
- Double-clicking `AffStamp.exe` gives the prompt-driven menu for whoever is running the final step; the CLI is there for you.

---

## Acrobat XI gotchas at the final step

After stamping, in Acrobat XI you can verify and certify, but **do not**:

- `File → Save As Other → Optimized PDF` (Discard Objects has "Discard external cross references" ticked by default)
- `File → Save As Other → Reduce File Size`
- `Tools → Protection → Sanitize Document` (strips annotations wholesale)
- `Print → Adobe PDF` / Microsoft Print to PDF
- Re-running OCR on the output

Safe: `Tools → Sign & Certify → Certify (Visible/Invisible Signature)`. That writes an incremental update and leaves your link annotations byte-identical. Pick *"Form filling and annotations allowed"* or *"No changes allowed"* — that, not flattening, is the real defence against the strip being edited out.

Last thing worth doing while you're set up: extract the text of the OCR'd scan and of the Word export and diff them page by page, so you have a record that the wording of the composite matches what was actually signed. `mutool draw -F txt` or a five-line PyMuPDF loop will do it, and it's cheap insurance if anyone ever queries the composite.


