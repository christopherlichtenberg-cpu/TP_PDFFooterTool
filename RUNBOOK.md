# AffStamp — runbook

Overlays the wet-ink signatures from a scanned affidavit onto the hyperlinked
PDF, **without disturbing the hyperlinks**.

A PDF hyperlink is an *annotation* — an object attached to the page, stored
separately from the content stream that holds the drawn marks. Adding an image
to the content stream cannot damage a link. AffStamp only ever adds, and saves
incrementally, so the Word export survives byte-for-byte inside the output.

Version 1.2.0.

---

## Before you start

Put everything in one folder, for example:

```
C:\Affidavit\
    hyperlinked.pdf        <- Word export, links already inserted
    signed_scan.pdf        <- the scan of the wet-signed hardcopy
    Exhibits\
        DOC-001.pdf
        DOC-002.pdf
```

**Double-click `AffStamp.exe`.** A window opens — that is all you need.

First time on a new machine, click **Self-test**. It should finish with
`SELFTEST PASSED` in the Output pane. That proves the tool runs there
before you touch the real files.

---

## The window

```
+-- Files ------------------------------------------------------+
|  Hyperlinked PDF  [ ...\hyperlinked.pdf     ]  [ Browse... ]  |
|  Scan PDF         [ ...\signed_scan.pdf     ]  [ Browse... ]  |
|  Output folder    [ C:\Affidavit            ]  [ Change... ]  |
+-- Settings ---------------------------------------------------+
|  Strip height [24] mm    Edge trim [2] mm                     |
|  Nudge  dx [0] mm (+right)    dy [0] mm (+down)               |
+-- Steps ------------------------------------------------------+
|  [1. Check / repair links] [2. Ghost overlay] [3. Measure]     |
|  [Ruler PDF]                                                  |
|  Trial pages [1-3]   [x] Replace the final page with the scan |
|  [4. Trial stamp]  [5. FULL STAMP]  [Audit]  [Self-test]      |
+-- Output -----------------------------------------------------+
|  everything each step prints, with warnings highlighted        |
+---------------------------------------------------------------+
|  [Open output folder] [Open last file] [Save log...] [Clear]   |
```

**Choose the two PDFs once, at the top.** Every step uses them, and they
are remembered next time you open the tool. Setting the hyperlinked PDF
also points the output folder at wherever it lives.

**Work left to right through the numbered buttons.** The unnumbered ones
(Ruler PDF, Audit, Self-test) are optional.

**Everything the tool writes goes to the Output folder.** You never type a
path. **Open output folder** and **Open last file** are at the bottom.

**Read the Output pane.** It is the same detail the command line gives —
warnings in amber, problems in red, confirmations in green. Those warnings
are the point of the tool, so do not skip past them. **Save log...** keeps
a copy.

Long steps run in the background: the buttons grey out and the bar at the
bottom right moves. The window stays responsive.

> There is also `AffStamp-cli.exe` in the same folder. Same tool, console
> instead of a window, with options the window does not expose. Run it with
> no arguments for a text menu, or see the Command line section at the end.

---

## Step 1 — Choose the two PDFs

Use the two **Browse...** buttons at the top. Setting the hyperlinked PDF
also points the output folder at the folder holding it; change that with
**Change...** if you want the results somewhere else.

The hyperlinked PDF must come from **File > Save As > PDF** in Word, or the
Acrobat PDFMaker ribbon — **not** Print to PDF, which throws the links away
before AffStamp ever sees the file.

---

## Step 2 — Check / repair the links

Click **1. Check / repair links**.

This lists every link annotation in the hyperlinked PDF and classifies it:

| Tag | Meaning |
|---|---|
| `REL` | relative path — what you want |
| `ABS` | absolute path (`C:\Users\...`) — will break on any other machine |
| `WEB` | http / https / mailto |
| `INTERNAL` | a jump to another page of the same document |

Word writes `ABS` when the Hyperlink Base is not set
(*File → Info → Properties → Advanced Properties → Summary → Hyperlink base*).
Mac Word does it more or less regardless.

When it finishes it asks whether to repair them. Say yes only if the output
flagged links as `ABS`. It writes `<name>_fixed.pdf` and **switches the
Hyperlinked PDF box to that file automatically**.

Repair rewrites each absolute target as a path relative to the output folder.
Where the original path sat outside that folder — a different drive, a network
share, another machine's user profile — it falls back to the bare file name
and says so in the summary. Those are the ones to check by hand: they only
resolve if the exhibit is sitting beside the affidavit.

If the tool reports **no link annotations at all**, stop. The export dropped
them and there is nothing to protect; re-export from Word properly first.

---

## Step 3 — Ghost overlay

Click **2. Ghost overlay**. This is the step that catches the problem you
cannot fix later.

It writes `GHOST.pdf` — **Open last file** shows it. Open it in Acrobat and
zoom in. The scan is drawn in **red** over the hyperlinked page:

- Red text sitting exactly on top of the black text — the two documents line
  up. Carry on.
- Red text offset by the same amount everywhere — set **Nudge dx / dy** to
  correct it.
- Red text fanning out, or twisting across the page — the scan is skewed.
  Deskew it first (`ocrmypdf --deskew --clean signed_scan.pdf deskewed.pdf`,
  or ScanTailor) and use the deskewed file as the scan. No nudge will fix a
  rotation.
- Red text a consistent fraction larger or smaller — the scan is a different
  page size. AffStamp scales for that automatically; the ghost is just how
  you find out.

`GHOST.pdf` is a check only. Never send it to anyone.

---

## Step 4 — Measure

Click **3. Measure**. Output looks like:

```
  (ignoring 3.8 mm of scanner shadow at the page edges)
  p1    11.9-15.0mm [x 150-192mm]   19.4-22.3mm [x 25-59mm]
  p2    11.9-15.0mm [x 150-192mm]   19.4-22.3mm [x 25-59mm]
  ...
Highest ink found          : 22.3 mm above the page edge
Horizontal extent          : 25 - 192 mm from the left edge
Scanner shadow at the edge : 3.8 mm
SUGGESTED  --height 27 --edge 3.8

SUGGESTED (right-hand column only - safer)
   --height 19 --edge 3.8 --left 147 --right 195
```

Read it as: each line is one page, and each band is `bottom-top mm` measured
**up from the bottom edge**, with the horizontal extent of that band in
brackets. Above, the **11.9–15.0 mm** band on the right (x 150–192) is the
initials; the **19.4–22.3 mm** band running in from the left margin is the
printed footer, which your hyperlinked PDF regenerates for itself and which
may carry a link.

Because signatures are placed by hand, the suggestion comes from the worst
page, not a typical one. It is written straight into the **Strip height**
box for you; override it there if you disagree.

- **Edge trim.** A flatbed scan of a page smaller than the platen leaves a
  black band at the edge. Measure works out the trim and fills in the **Edge
  trim** box, otherwise every page would get a black bar across the foot.
- **The second suggestion is usually the right one.** Lifting only the
  right-hand column cannot cover a footer link. Type its `--left` / `--right`
  values into `AffStamp-cli.exe` if you want that box, or lower the strip
  height in the window so it clears the printed footer.
- **Measure also checks the hyperlinked PDF.** It reports anything printed
  inside the strip zone, and warns about **link annotations** that would end
  up hidden under scanned ink. A covered link still works, which is worse than
  a broken one — it looks like a bug to whoever reads it.

To eyeball it, **Ruler PDF** writes a copy of the scan with a millimetre
grid and a green box showing exactly what would be lifted. Open it in
Acrobat, zoom the footer, adjust, re-run. It is a few seconds per pass.

---

## Step 5 — Trial stamp

Click **4. Trial stamp**. Do this before the full run, every time.

It writes `TEST.pdf`, covering just the pages in the **Trial pages** box.
**Print a page and hold it against the original.** That is the only
reliable way to catch a small offset or scanner skew.

If the marks sit slightly off, type a correction into the **Nudge** boxes:

```
dx  positive moves the strip right
dy  positive moves the strip down
```

The nudge is remembered and applied to every later run. Click **4. Trial
stamp** again until a printed page lines up.

If only *some* pages are off, or the scan has a stray cover sheet so the page
numbers do not match, use an offsets file — see the Command line section.

**If the ink itself looks wrong**, these are `AffStamp-cli.exe` options —
tell me what you are seeing and I will give you the exact line to run:

| Symptom | Option |
|---|---|
| faint initials disappearing | `--white 215` (up to 225) |
| grey haze or dirty background | `--white 190` (down to 185) |
| strokes look washed out | `--black 40` |
| heavy speckle | leave despeckling on (it is on by default) |

Leave the ink black. `--ink` accepts a hex colour, but recolouring a
signature on an affidavit to "look like the original blue" is exactly the
kind of thing you do not want to have to explain later.

---

## Step 6 — The full run

Tick or clear **Replace the final page with the scan**, then click
**5. FULL STAMP**. A confirmation appears first, spelling out exactly what
is about to happen — including a warning if the final page carries
hyperlinks that the replacement would destroy.

**Ticked** — the final page is swapped wholesale for the scan's, resized to
the base page dimensions so the document keeps uniform page sizes. Use this
when the last page carries a full execution block (signature, witness,
jurat) that a footer strip would not capture.

**Cleared** — the final page is left completely untouched, for you to
replace yourself in Acrobat.

Either way **the last page is never stamped**, because a footer strip would
not capture an execution block.

The scanned page has no hyperlinks of its own, so replacing it destroys any
that were on that page. The tool lists exactly which ones went and keeps them
separate from the integrity check, so you get:

```
OK - all 127 link annotations preserved.
   (1 link(s) went with the replaced final page, as asked.)
OK - the hyperlinked PDF is unmodified inside the output (byte-identical prefix).
```

rather than a mismatch over a deletion you asked for.

It writes, into the output folder:

| File | What it is |
|---|---|
| `<name>_SIGNED.pdf` | the composite |
| `<name>_SIGNED_links.txt` | every surviving link target, page by page |
| `<name>_SIGNED.manifest.json` | settings, SHA-256 of both inputs and the output, which pages were stamped, and `removed_with_final_page` |

Keep the manifest. It is the record of what was done to produce the composite.

**If you see `LINK MISMATCH ... DO NOT USE THIS FILE`, stop and say so.**
That is the tool refusing to hand you a broken bundle.

---

## The optional audit

The **Audit** button compares the wording of the scan against the wording of
the hyperlinked PDF, page by page, and reports a similarity percentage.

It needs a text layer in the scan, so run OCR on the **scan** first — never
on the output:

```
ocrmypdf --deskew --clean signed_scan.pdf scan_ocr.pdf
```

then audit against `scan_ocr.pdf`. Some difference is normal: OCR misreads,
and the scan has handwriting the draft does not. Read any flagged page side
by side before drawing a conclusion.

Worth one run on a document you have not seen before, ignorable otherwise.
What it buys you is a record that the wording of the composite matches what
was actually signed.

---

## After the stamp — in Acrobat

The strip is an image baked into the page content stream. It is not an
annotation, stamp or shape, so it cannot be selected, dragged or deleted
without full content editing.

**Safe:** `Tools → Sign & Certify → Certify (Visible/Invisible Signature)`.
That writes an incremental update and leaves your link annotations
byte-identical. Choose *"Form filling and annotations allowed"* or
*"No changes allowed"*. Certifying — not flattening — is the real defence
against the strip being edited out, and it is stronger evidence.

**Never**, on the output file:

- `File → Save As Other → Optimized PDF` — *Discard external cross references*
  is ticked by default
- `File → Save As Other → Reduce File Size`
- `Tools → Protection → Sanitize Document` — strips annotations wholesale
- `Print → Adobe PDF` or Microsoft Print to PDF
- re-running OCR on the output

Then re-verify the links in Acrobat **and** in whatever viewer the recipient
will use. Relative `/Launch` file links behave differently across Acrobat,
Edge, Chrome and DMS viewers, and some block them entirely unless the whole
bundle sits in a trusted folder.

---

## One non-technical caveat

You are producing a document that is neither the executed original nor the
unexecuted draft. That is normal for an e-filed hyperlinked bundle, but the
safe framing is: **the scanned PDF remains the executed affidavit; the
hyperlinked composite is a navigable working copy.** Keep the original scan
in the bundle, and check your court or registry rules before serving the
composite as the affidavit itself.

---

## Command line

The window covers everything, but each step is also a command, and the
commands take options the window does not expose. Use `AffStamp-cli.exe`:
run it with no arguments for a text menu, or
`AffStamp-cli.exe <command> --help` for the full option list.

| Command | What it does |
|---|---|
| `links` | list and repair link targets |
| `ghost` | the alignment overlay |
| `measure` | find the ink, suggest height and trim |
| `ruler` | mm grid and proposed box |
| `stamp` | the work |
| `audit` | compare the wording |
| `gui` | open the window |
| `selftest` | prove the tool works on this machine |

A full run, spelled out:

```
AffStamp-cli.exe stamp --base hyperlinked.pdf --scan signed_scan.pdf ^
    --height 19 --edge 3.8 --left 147 --right 195 ^
    --dx -1.5 --dy 0.8 --replace-last
```

Trial the same settings on three pages by adding `--pages 1,2,40`; the output
is named `TEST.pdf` automatically.

**Per-page corrections.** If individual pages are off, or the scan has a
stray cover sheet, pass `--offsets offsets.csv`:

```csv
page,dx_mm,dy_mm,scan_page
7,0,-1.2,7
8,1.0,0,9
```

`page` is the page of the hyperlinked PDF, `scan_page` the page of the scan
to lift from. Both nudges are added to the global `--dx` / `--dy`.

---

## If something goes wrong

| Message | What to do |
|---|---|
| `no link annotations at all` | The Word export dropped them. Re-export with Save As, not Print to PDF. |
| `PAGE COUNT MISMATCH` | Use `--offsets` with a `scan_page` column, or fix the scan. |
| `strip came out blank` | The strip is above the ink, or the scan is faint. Re-run Measure, or raise `--white`. |
| `LINK MISMATCH ... DO NOT USE THIS FILE` | Do not use it. Send me the link report. |
| `rotated pages` | Normalise the rotation first or the strip lands on the wrong edge. |
| `incremental save refused` | The links are still fine, but the original bytes are no longer a prefix. Usually means the input is encrypted. |
| window will not open | Run `AffStamp-cli.exe selftest`. If that passes, the machine is missing something the window needs — use the console menu. |
