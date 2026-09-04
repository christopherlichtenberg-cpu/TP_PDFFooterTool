#!/usr/bin/env python3
"""
Regression tests for AffStamp.  No test framework needed:

    py tests/test_affstamp.py

Builds its own PDF fixtures, so it runs anywhere the tool runs.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import affstamp as A
import pymupdf as fitz

FAILURES = []
QUIET = lambda msg, level="info": None


def check(name, condition, detail=""):
    ok = bool(condition)
    print(("  ok   " if ok else "  FAIL ") + name +
          ("" if ok or not detail else "   <- %s" % (detail,)))
    if not ok:
        FAILURES.append(name)


def section(title):
    print("\n%s" % title)


def region_delta(page_a, page_b, rect, dpi=150):
    """(mean, max) absolute grey-level difference over a region.

    Rendering the same content out of two differently laid out files gives
    +/-1 grey level of anti-aliasing noise, so "unchanged" means a mean well
    under one level - not an exact match.
    """
    from PIL import Image, ImageChops
    def grey(page):
        pix = page.get_pixmap(dpi=dpi, clip=rect, colorspace=fitz.csGRAY)
        return Image.frombytes("L", (pix.width, pix.height), pix.samples)
    diff = ImageChops.difference(grey(page_a), grey(page_b))
    hist = diff.histogram()
    count = sum(hist) or 1
    mean = sum(i * n for i, n in enumerate(hist)) / float(count)
    return mean, diff.getextrema()[1]


# ---------------------------------------------------------------- fixtures

def make_base(path, pages=4, links="rel", link_in_footer=True):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=A.mm(210), height=A.mm(297))
        page.insert_text((A.mm(25), A.mm(40)), "AFFIDAVIT", fontsize=14)
        for line in range(14):
            page.insert_text((A.mm(25), A.mm(52 + line * 7)),
                             "%d. Exhibit DOC-%03d is produced." % (line + 1, i * 14 + line + 1),
                             fontsize=10)
        page.insert_text((A.mm(25), A.mm(276)), "DOC-%03d" % (i + 1), fontsize=9)
        if link_in_footer:
            rect = fitz.Rect(A.mm(25), A.mm(272), A.mm(60), A.mm(278))
            if links == "abs":
                page.insert_link({"kind": fitz.LINK_URI,
                                  "uri": "file:///C:/Nowhere/Exhibits/DOC-%03d.pdf" % (i + 1),
                                  "from": rect})
            else:
                page.insert_link({"kind": fitz.LINK_LAUNCH,
                                  "file": "Exhibits/DOC-%03d.pdf" % (i + 1),
                                  "from": rect})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_scan(path, pages=4, shadow_mm=3.0, ink=True):
    build = fitz.open()
    for i in range(pages):
        page = build.new_page(width=A.mm(209), height=A.mm(296))
        page.insert_text((A.mm(25), A.mm(40)), "AFFIDAVIT", fontsize=14)
        for line in range(14):
            page.insert_text((A.mm(25), A.mm(52 + line * 7)),
                             "%d. Exhibit DOC-%03d is produced." % (line + 1, i * 14 + line + 1),
                             fontsize=10)
        page.insert_text((A.mm(25), A.mm(276)), "DOC-%03d" % (i + 1), fontsize=9)
        if ink:
            x, y = A.mm(150), A.mm(284)
            for k in range(6):
                page.draw_bezier((x + k * A.mm(7), y),
                                 (x + k * A.mm(7) + A.mm(2), y - A.mm(8)),
                                 (x + k * A.mm(7) + A.mm(4), y + A.mm(5)),
                                 (x + k * A.mm(7) + A.mm(7), y - A.mm(3)),
                                 color=(0.08, 0.08, 0.08), width=1.8)
        if shadow_mm:
            page.draw_rect(fitz.Rect(0, page.rect.y1 - A.mm(shadow_mm),
                                     page.rect.x1, page.rect.y1),
                           color=(0.1, 0.1, 0.1), fill=(0.1, 0.1, 0.1))
    flat = fitz.open()
    for i in range(pages):
        pix = build[i].get_pixmap(dpi=200, colorspace=fitz.csGRAY)
        page = flat.new_page(width=build[i].rect.width,
                             height=build[i].rect.height)
        page.insert_image(page.rect, pixmap=pix)
    flat.save(path, garbage=3, deflate=True)
    flat.close()
    build.close()


# ------------------------------------------------------------------- tests

def test_units():
    section("units and parsing")
    check("mm round trip", abs(A.pt(A.mm(25)) - 25) < 1e-9)
    check("pages 1-3", A.parse_pages("1-3", 10) == [0, 1, 2])
    check("pages 1,2,40 clipped to length", A.parse_pages("1,2,40", 5) == [0, 1])
    check("pages blank means all", A.parse_pages("", 3) == [0, 1, 2])
    check("pages dedupes", A.parse_pages("1,1,2", 5) == [0, 1])
    check("colour black", A.parse_colour("black") == (0, 0, 0))
    check("colour hex", A.parse_colour("#1A3FBB") == (26, 63, 187))
    try:
        A.parse_colour("nope")
        check("bad colour rejected", False)
    except ValueError:
        check("bad colour rejected", True)


def test_alpha_lut():
    section("ink extraction curve")
    lut = A.build_alpha_lut(205, 60)
    check("paper is transparent", lut[255] == 0 and lut[205] == 0)
    check("solid ink is opaque", lut[0] == 255 and lut[60] == 255)
    check("mid grey is partial", 0 < lut[130] < 255, lut[130])
    check("curve is monotonic",
          all(lut[i] >= lut[i + 1] for i in range(255)))
    degenerate = A.build_alpha_lut(10, 200)      # black above white
    check("degenerate thresholds do not crash", len(degenerate) == 256)


def test_relative_target():
    section("link relativisation")
    cases = [
        ("file:///C:/Affidavit/Exhibits/DOC-001.pdf", "C:/Affidavit", "Exhibits/DOC-001.pdf"),
        ("/C:/Affidavit/Exhibits/DOC-001.pdf",        "C:\\Affidavit", "Exhibits/DOC-001.pdf"),
        ("C:/Affidavit/DOC-001.pdf",                  "C:/Affidavit", "DOC-001.pdf"),
        ("C:/Users/someone/Bundle/DOC-1.pdf",         "C:/Affidavit", "DOC-1.pdf"),
        ("D:/Elsewhere/DOC-1.pdf",                    "C:/Affidavit", "DOC-1.pdf"),
        ("//server/share/DOC-1.pdf",                  "C:/Affidavit", "DOC-1.pdf"),
        ("file:///C:/Affidavit/Ex/DOC%20A.pdf",       "C:/Affidavit", "Ex/DOC A.pdf"),
        ("/home/u/bundle/Ex/D.pdf",                   "/home/u/bundle", "Ex/D.pdf"),
    ]
    for target, anchor, want in cases:
        got, _ = A.relative_target(target, anchor)
        check("%-42s -> %s" % (target, want), got == want, got)


def test_pipeline(work):
    section("full pipeline")
    base = os.path.join(work, "hyperlinked.pdf")
    scan = os.path.join(work, "signed_scan.pdf")
    make_base(base, links="abs")
    make_scan(scan)

    # links: detect and repair
    a = A.args_for("links", {"base": base, "repair": True})
    check("links repair returns 0", A.cmd_links(a) == 0)
    fixed = os.path.join(work, "hyperlinked_fixed.pdf")
    check("wrote the repaired file", os.path.isfile(fixed))
    check("session switched to it", a.base == fixed, a.base)
    targets = [A.link_target(lk) for p in fitz.open(fixed) for lk in p.get_links()]
    check("no absolute targets remain",
          all(A.classify_link({"kind": fitz.LINK_LAUNCH, "file": t}) != "ABS"
              for t in targets), targets[:2])

    # measure
    a = A.args_for("measure", {"scan": scan, "base": fixed})
    check("measure returns 0", A.cmd_measure(a) == 0)
    check("suggested a height", a.measured_height > 0, a.measured_height)
    check("found the scanner shadow", a.measured_edge >= 2.5, a.measured_edge)

    # ruler and ghost
    check("ruler returns 0", A.cmd_ruler(A.args_for(
        "ruler", {"scan": scan, "base": fixed, "height": 19, "edge": 3.8,
                  "pages": "1-2"})) == 0)
    check("ruler file written",
          os.path.isfile(os.path.join(work, "signed_scan_RULER.pdf")))
    check("ghost returns 0", A.cmd_ghost(A.args_for(
        "ghost", {"base": fixed, "scan": scan, "pages": "1-2"})) == 0)
    check("ghost file written", os.path.isfile(os.path.join(work, "GHOST.pdf")))

    # trial stamp
    a = A.args_for("stamp", {"base": fixed, "scan": scan, "height": 19,
                             "edge": 3.8, "left": 147, "right": 195,
                             "pages": "1-2"})
    check("trial stamp returns 0", A.cmd_stamp(a) == 0)
    test_pdf = os.path.join(work, "TEST.pdf")
    check("trial named TEST.pdf", os.path.isfile(test_pdf))

    # full run with final page replacement
    a = A.args_for("stamp", {"base": fixed, "scan": scan, "height": 19,
                             "edge": 3.8, "left": 147, "right": 195,
                             "replace_last": True})
    check("full stamp returns 0", A.cmd_stamp(a) == 0)
    signed = os.path.join(work, "hyperlinked_fixed_SIGNED.pdf")
    check("wrote _SIGNED.pdf", os.path.isfile(signed))
    check("wrote the manifest", os.path.isfile(
        os.path.join(work, "hyperlinked_fixed_SIGNED.manifest.json")))
    check("wrote the link report", os.path.isfile(
        os.path.join(work, "hyperlinked_fixed_SIGNED_links.txt")))

    import json
    manifest = json.load(open(os.path.join(
        work, "hyperlinked_fixed_SIGNED.manifest.json")))
    check("manifest records the removed final-page link",
          len(manifest["removed_with_final_page"]) == 1,
          manifest["removed_with_final_page"])
    check("manifest says links were preserved", manifest["links"]["preserved"])
    check("manifest stamped 3 of 4 pages (last is never stamped)",
          len(manifest["pages_stamped"]) == 3,
          len(manifest["pages_stamped"]))
    check("manifest saved incrementally", manifest["output"]["incremental_save"])

    # the properties that matter
    before = A.link_fingerprint(fitz.open(fixed))
    after = A.link_fingerprint(fitz.open(signed))
    check("links on pages 1-3 survived", len(after) == len(before) - 1,
          (len(before), len(after)))
    original = open(fixed, "rb").read()
    check("base bytes are an intact prefix",
          open(signed, "rb").read()[:len(original)] == original)
    out = fitz.open(signed)
    check("page count unchanged", out.page_count == 4, out.page_count)
    check("replaced page resized to the base dimensions",
          abs(out[3].rect.width - A.mm(210)) < 0.5, out[3].rect.width)

    # Untouched pages must be untouched at the byte level. TEST.pdf stamped
    # only pages 1-2, so 3 and 4 are the control. This is a far stronger
    # statement than comparing rendered pixels, which carry +/-1 grey level
    # of anti-aliasing jitter whenever the xref layout changes.
    src = fitz.open(fixed)
    trial = fitz.open(test_pdf)
    check("unstamped pages are byte-identical",
          all(src[i].read_contents() == trial[i].read_contents()
              for i in (2, 3)))
    check("stamped page did gain content",
          len(trial[0].read_contents()) > len(src[0].read_contents()))
    check("no image was added to an unstamped page",
          not trial[2].get_xobjects() and not trial[3].get_xobjects())

    # ink really landed, and only where asked
    zone = fitz.Rect(A.mm(140), A.mm(272), A.mm(205), A.mm(292))
    darkness = lambda d, i, r: sum(d[i].get_pixmap(dpi=72, clip=r,
                                                   colorspace=fitz.csGRAY).samples)
    check("strip zone got darker", darkness(out, 0, zone) < darkness(src, 0, zone))

    footer = fitz.Rect(A.mm(20), A.mm(270), A.mm(70), A.mm(280))
    bottom = fitz.Rect(0, A.mm(294), A.mm(210), A.mm(297))
    mean_d, max_d = region_delta(src[0], out[0], footer)
    check("the printed footer was not painted over",
          mean_d < 0.5 and max_d <= 4, "mean %.3f max %d" % (mean_d, max_d))
    mean_d, max_d = region_delta(src[0], out[0], bottom)
    check("no scanner shadow bar at the foot",
          mean_d < 0.5 and max_d <= 4, "mean %.3f max %d" % (mean_d, max_d))
    mean_d, max_d = region_delta(src[0], out[0], zone)
    check("the signature zone did change materially",
          mean_d > 2.0, "mean %.3f" % mean_d)


def test_guards(work):
    section("refusals and guard rails")
    base = os.path.join(work, "g_base.pdf")
    scan = os.path.join(work, "g_scan.pdf")
    make_base(base, pages=3)
    make_scan(scan, pages=2)

    a = A.args_for("stamp", {"base": base, "scan": scan, "height": 19})
    check("page count mismatch is refused", A.cmd_stamp(a) == 2)

    a = A.args_for("stamp", {"base": base, "scan": base, "out": base,
                             "height": 19})
    check("refuses to overwrite the input", A.cmd_stamp(a) == 2)

    blank = os.path.join(work, "blank_scan.pdf")
    make_scan(blank, pages=3, shadow_mm=0, ink=False)
    a = A.args_for("stamp", {"base": base, "scan": blank, "height": 12,
                             "left": 147, "right": 195,
                             "out": os.path.join(work, "blank_out.pdf")})
    rc = A.cmd_stamp(a)
    import json
    manifest = json.load(open(os.path.join(work, "blank_out.manifest.json")))
    check("blank strip is reported, not stamped",
          rc == 0 and manifest["pages_blank"] == [1, 2],
          (rc, manifest["pages_blank"]))

    missing = A.args_for("measure", {"scan": os.path.join(work, "nope.pdf")})
    try:
        A.cmd_measure(missing)
        check("missing file raises", False)
    except ValueError:
        check("missing file raises", True)


def test_state(work):
    section("saved session state")
    os.environ["XDG_DATA_HOME"] = os.path.join(work, "state")
    if os.name == "nt":
        os.environ["LOCALAPPDATA"] = os.path.join(work, "state")
    A.clear_state()
    s = A.Session(load=False)
    s.base = os.path.join(work, "hyperlinked.pdf")
    s.scan = os.path.join(work, "signed_scan.pdf")
    s.height, s.edge, s.dx, s.dy = 19.0, 3.8, -1.5, 0.8
    s.trial_pages = "1-2"
    s.save()
    again = A.Session()
    check("base restored", again.base == s.base)
    check("height restored", again.height == 19.0)
    check("nudge restored", (again.dx, again.dy) == (-1.5, 0.8))
    check("trial pages restored", again.trial_pages == "1-2")
    A.clear_state()
    check("reset clears it", not A.Session().base)


def main():
    print("AffStamp %s regression tests" % A.VERSION)
    work = tempfile.mkdtemp(prefix="affstamp_tests_")
    saved_writer = A._WRITER
    try:
        test_units()
        test_alpha_lut()
        test_relative_target()
        A.set_writer(QUIET)          # the commands are chatty; keep the log clean
        test_pipeline(work)
        test_guards(work)
        test_state(work)
        A.set_writer(saved_writer)
        section("self-test")
        check("selftest passes", A.cmd_selftest(A.args_for("selftest", {})) == 0)
    finally:
        A.set_writer(saved_writer)
        shutil.rmtree(work, ignore_errors=True)

    print("\n%s" % ("=" * 60))
    if FAILURES:
        print("%d FAILURE(S):" % len(FAILURES))
        for name in FAILURES:
            print("   %s" % name)
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
