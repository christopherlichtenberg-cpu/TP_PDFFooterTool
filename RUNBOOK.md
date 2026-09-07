# AffStamp — runbook

Overlays the wet-ink signatures from the scanned affidavit onto the
hyperlinked PDF, without disturbing the hyperlinks.

**Why this works:** in a PDF, hyperlinks are *annotations* attached to the
page. Drawn marks live in a completely separate object, the *content
stream*. Adding an image to the content stream cannot touch the
annotations. Every step below is built on that, and the tool checks the
link count before and after to prove it.

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

**Double-click `AffStamp.exe`.** A window opens - that is all you need.

First time on a new machine, click **Self-test**. It should finish with
`SELFTEST PASSED` in the Output pane. That proves the tool runs there
before you touch the real files.

---

## The window

```
+-- Files ------------------------------------------------------+
|  Hyperlinked PDF  [ ...\hyperlinked.pdf     ]  [ Browse... ] |
|  Scan PDF         [ ...\signed_scan.pdf     ]  [ Browse... ] |
|  Output folder    [ C:\Affidavit            ]  [ Change... ] |
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

**Read the Output pane.** It is the same detail the command line gives -
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

---

## Step 2 — Check and repair the hyperlinks

Click **1. Check / repair links**.

This lists every link and flags two problems:

- **`ABS`** — Word turned a relative link into an absolute one
  (`file:///C:/Users/...`). These break the moment the bundle is copied
  anywhere else.
- **missing targets** — a link pointing at a file that does not exist,
  usually a mistyped Document ID.

When it finishes it asks whether to repair them. Say yes only if the output
flagged links as `ABS`. It writes `<name>_fixed.pdf` and **switches the
Hyperlinked PDF box to that file automatically**.

---

## Step 3 — Check the pagination

Click **2. Ghost overlay**. This is the step most likely to sink the whole job.

Inserting hyperlinks into the Word document changes character formatting,
which can change line breaks, which can change **page** breaks. If the
hyperlinked PDF paginates differently from the signed original, the
initials land on the wrong pages.

It writes `GHOST.pdf` - **Open last file** shows it. Open it in Acrobat and flip through every page. Each
page shows the hyperlinked PDF in **black** with the scan laid over it in
**red**.

| What you see | What it means |
|---|---|
| Red sitting exactly on black | Aligned and paginated identically. Good. |
| Red consistently a little below/right of black | A uniform offset. You will correct it with a nudge in step 5. |
| Red drifting further from black towards one edge | The scan is skewed. See "If the scan is skewed" below. |
| Two completely different pages superimposed | **A page break has moved. Stop.** Fix the Word document so it paginates like the signed original, re-export, and start again. |

Matching page counts does **not** prove matching pagination — you have to
look.

---

## Step 4 — Work out the strip height

Click **3. Measure**. Output looks like:

```
  p  1: 12.4-19.6mm [x 30-169]   40.4-43.2mm [x 21-43] printed
  ...
Tallest band unique to the scan : 20.0 mm  (page 3)
SUGGESTED  --height 24
```

Read it as: the initials occupy 12–20 mm up from the bottom edge. The
40–43 mm band is marked `printed` because the Word export has it too, so it
is regenerated on the base page and must **not** be lifted.

Because signatures are placed by hand, the suggestion comes from the worst
page, not a typical one. It is written straight into the **Strip height**
box for you; override it there if you disagree.

It also warns you about two things:

- **scanner shadow** — a dark band along the very bottom edge of the scan.
  It works out the trim and fills in the **Edge trim** box, otherwise every
  page would get a black bar across the foot.
- **links inside the strip zone** — a link that gets painted over still
  works but is invisible. If it reports any, use a smaller height.

To eyeball it, **Ruler PDF** writes a copy of the scan with a millimetre
grid and a green box showing exactly what would be lifted.

---

## Step 5 — Trial run

Click **4. Trial stamp**. Do this before the full run, every time.

It writes `TEST.pdf`, covering just the pages in the **Trial pages** box.
**Print a page and hold it against the original.** That is the only
reliable way to catch a small offset.

If the marks sit slightly off, type a correction into the **Nudge** boxes:

- **dx** shifts sideways: positive = right
- **dy** shifts vertically: positive = down

The nudge is remembered and applied to every later run. Click **4. Trial
stamp** again until a printed page lines up.

**If the ink itself looks wrong**, these are `AffStamp-cli.exe` options -
tell me what you are seeing and I will give you the exact line to run:

| Problem | Fix |
|---|---|
| Faint initials disappearing | raise `--white` to 215–225 |
| Grey haze around the ink | lower `--white` to 185–195 |
| Strokes look washed out | lower `--black` to 40 |

Leave the ink black. It can be recoloured, but recolouring a signature on
an affidavit to "look like the original blue" is not something you want to
have to explain later.

---

## Step 6 — Full run

Tick or clear **Replace the final page with the scan**, then click
**5. FULL STAMP**. A confirmation appears first, spelling out exactly what
is about to happen - including a warning if the final page carries
hyperlinks that the replacement would destroy.

**Ticked** — the final page is swapped wholesale for the scan's final page,
resized to match the rest of the document. Use this when the last page
carries the full execution block and jurat rather than just initials, which
is the normal case. You do not then need to replace it by hand in Acrobat.

**Cleared** — the final page is left completely untouched, for you to
replace yourself in Acrobat.

Either way the final page is never stamped, because a strip would not
capture a full signature block.

> **The one thing to watch:** the scanned page has no hyperlinks. If the
> final page of the Word document had Document ID links on it, replacing
> the page destroys them. The tool lists exactly which links went, so you
> can re-add them in Acrobat. If the last page has no links, there is
> nothing to worry about.

Read the last lines carefully:

```
OK  - all 128 link annotations preserved.
```

or, if you replaced the final page:

```
OK  - all 127 remaining link annotations preserved.
      1 link(s) were removed with the replaced final page.
```

If it says **LINK MISMATCH**, do not use the file — tell me what it says.
If it reports pages with **no signature ink**, check those pages in the
scan: every page should carry both marks, and a page genuinely missing one
is something you need to know about.

It writes three files beside the hyperlinked PDF:

- `<name>_SIGNED.pdf` — the composite
- `<name>_SIGNED_manifest.json` — a record of the run: input and output
  SHA-256 hashes, every parameter used, per-page results, link counts,
  and any links removed with the final page. Keep this.
- `<name>_SIGNED_links.txt` — every link in the output

The save is **incremental**: the original bytes of the hyperlinked PDF are
left completely untouched and everything new is appended. That means you
can prove the composite contains the Word export unmodified.

---

## Step 7 — Verify, then certify

Open the output in Acrobat, check a few pages and the final page, and click
some links to confirm they open the right exhibit.

If the final page was replaced and it had links, re-add them now.

Then certify — this is what actually stops the signatures being edited out.
The stamped ink is baked into the page content so it cannot be clicked,
dragged or deleted like a stamp or annotation, but Acrobat's content
editing could still remove it. Certification makes any later alteration
visible.

`Tools → Sign & Certify → Certify (Visible or Invisible Signature)`

Choose **"Form filling and annotations allowed"** or **"No changes
allowed"**. Acrobat will offer to create a self-signed digital ID if you do
not have one. Certifying appends an incremental update and does not touch
the link annotations.

**Certify last.**

---

## Never do these to the finished file

Each of these will strip or break the hyperlinks:

- `File → Save As Other → Optimized PDF` (Discard Objects has "discard
  external cross references" ticked by default)
- `File → Save As Other → Reduce File Size`
- `Tools → Protection → Sanitize Document`
- `Print → Adobe PDF`, or Microsoft Print to PDF
- Re-running OCR on the output

---

## The optional audit

Wet-signed documents sometimes carry initialled amendments in the body of a
page — a crossing-out, a correction, a date written in. A footer-only
overlay would lose them.

The **Audit** button compares the scan against the Word export and reports anything on
the scan that the Word export does not contain, above the strip. Scanner
skew and noise cause false positives, so check anything it reports against
the ghost PDF before acting on it.

Worth running once on a document you have not seen before. Skip it if you
already know there are no handwritten amendments.

---

## If the scan is skewed

If the ghost overlay shows the red drifting progressively away from the
black across the page, the scan is rotated by a fraction of a degree.

A small skew is usually tolerable — the strip is only ~24 mm tall, so the
drift across it is slight. Judge it from the printed trial page.

If it is bad enough to matter, deskew the scan first and use the deskewed
file as the scan:

```bash
ocrmypdf --deskew --clean signed_scan.pdf scan_deskewed.pdf
```

(OCR on the *scan* is fine. Never run OCR on the output.)

---

## File size

The signature layer adds roughly 20–30 KB per page, so a 40-page affidavit
grows by under a megabyte. If it grows by far more, the scan is unusually
noisy — say so and I will adjust the settings.

Replacing the final page adds more, because that whole page becomes an
image: expect a few hundred KB for it.

---

## Command line

The window covers everything, but each step is also a command, and the
commands take options the window does not expose. Use `AffStamp-cli.exe`:
run it with no arguments for a text menu, or
`AffStamp-cli.exe <command> --help` for the full option list.

| Command | Purpose |
|---|---|
| `gui` | open the window |
| `selftest` | prove the tool works on this machine |
| `links` | dump, repair (`--fix-relative`) and check (`--check`) hyperlinks |
| `compare` | red ghost overlay — pagination and alignment |
| `measure` | what strip height to use |
| `ruler` | scan copy with a millimetre grid |
| `stamp` | do the overlay |
| `audit` | handwriting on the scan that the Word export lacks |

Output paths default to the folder holding the hyperlinked PDF, so `--out`
is optional. `--out-dir` puts them somewhere else.

A full run from the command line:

```bash
AffStamp-cli.exe stamp --base hyperlinked.pdf --scan signed_scan.pdf ^
      --height 24 --edge 2 --replace-last
```

Use `--skip-last` instead of `--replace-last` to leave the final page for
Acrobat. `--pages 1-3,7` limits which pages are stamped. `--offsets
offsets.csv` gives per-page corrections:

```csv
page,dx_mm,dy_mm,scan_page,height
7,0,-1.2,7,24
8,1.0,0,9,24
```

The `scan_page` column also handles a scan whose pages do not line up 1:1
with the base — a stray cover sheet, for instance.
