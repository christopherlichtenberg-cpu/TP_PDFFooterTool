# AffStamp

Overlays the wet-ink signatures from a scanned affidavit onto a hyperlinked
PDF, **without disturbing the hyperlinks**.

You have two documents: a Word file whose Document ID references have been
hyperlinked to relative paths and exported to PDF, and a scan of the same
document signed in wet ink — with initials at the foot of *every* page, not
just the last. You need one PDF with both. Re-printing, flattening or
re-distilling the composite destroys the hyperlinks.

AffStamp lifts the ink out of the bottom of each scanned page, drops the paper
background out to genuine transparency, and lays the result onto the
corresponding page of the hyperlinked PDF as an image in the content stream.
Link annotations live outside the content stream, so they are untouched — and
the save is incremental, so the Word export survives **byte-for-byte** as a
prefix of the output file. Every run verifies both properties and refuses to
hand you a file that fails either.

## Quick start

Double-click `AffStamp.exe`. Click **Self-test** once on a new machine, choose
the two PDFs, then work left to right through the numbered buttons.

**[RUNBOOK.md](RUNBOOK.md) is the step-by-step process.** Read that.

## What it does

| Step | |
|---|---|
| **Check / repair links** | Lists every link annotation and flags absolute targets Word left behind; rewrites them relative to the bundle. |
| **Ghost overlay** | Draws the scan in red over the hyperlinked page so you can see whether they line up before committing to 40 pages. |
| **Measure** | Finds the ink bands at the foot of the scan, in millimetres, separates the signature column from printed footer text, detects scanner shadow, and warns about link annotations the strip would cover. |
| **Ruler PDF** | The scan with a millimetre grid and a green box showing exactly what would be lifted. |
| **Trial stamp** | The same operation on three pages, so you can print one and hold it against the original. |
| **Full stamp** | The work, plus optional wholesale replacement of the final page with the scan's, resized to the base page dimensions. |
| **Audit** | Compares the wording of the scan against the draft, page by page. |
| **Self-test** | Builds a fixture, runs the pipeline, asserts links preserved / ink stamped / base bytes intact. |

Each run writes the composite, a link report, and a manifest recording the
settings, the SHA-256 of both inputs and the output, which pages were stamped,
and any links removed with a replaced final page.

## Files

| | |
|---|---|
| `affstamp.py` | Engine, command line, and text menu. All the logic lives here. |
| `affstamp_gui.py` | Tkinter window. A thin shell over `affstamp.py`. |
| `AffStamp.spec` | PyInstaller: both executables from one shared runtime folder. |
| `build_exe.bat` | Rebuilds `dist\AffStamp\` on Windows. |
| `run.bat` | Opens the window from source (developer use). |
| `RUNBOOK.md` | The process, in order, with the Acrobat do-nots. |
| `START HERE.txt` | Ships inside the built folder for whoever runs the final step. |

## Running from source

```
py -m pip install -r requirements.txt
py affstamp_gui.py          # the window
py affstamp.py              # the text menu
py affstamp.py selftest     # prove it works here
```

Python 3.8+, two dependencies: PyMuPDF and Pillow. No numpy — Pillow's BOX
resize gives the row and column profiles the analysis needs, and dropping
numpy takes about 45 MB off the frozen build.

## Building the executables

On a Windows machine of the **same bitness as the target**:

```
build_exe.bat
```

That produces `dist\AffStamp\` containing `AffStamp.exe` (the window),
`AffStamp-cli.exe` (console, plus the ink-tuning options the window does not
expose), and the shared `_internal` folder. Ship the whole folder — the
executables will not run without `_internal` beside them.

Target requirement: 64-bit Windows 10 1607 or later. Nothing is installed and
no admin rights are needed; it is a folder copy.

Notes for locked-down machines:

- **Onedir, not onefile.** A onefile exe unpacks into `%TEMP%` and runs from
  there, which AppLocker and software-restriction policies commonly block.
- **Antivirus.** Unsigned PyInstaller binaries get flagged as
  `Wacatac`/`Trojan:Script/Wacapew` with depressing regularity. Get the folder
  hash allowlisted, or code-sign it, before promising anyone a delivery date.
- **Verify on the target**, not the build box: `AffStamp-cli.exe selftest`.

## Provenance

Reconstructed from the design conversation in
`Overlaying_Scanned Signatures on Hyperlinked PDF.md` plus the recovered
transcript of the build session. Behaviour, option names, output filenames and
the window layout match the 1.2.0 build; the Python source is a rewrite, not
the original bytes.
