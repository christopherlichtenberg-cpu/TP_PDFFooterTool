#!/usr/bin/env python3
"""
Regression tests for AffStamp.  No test framework needed:

    py tests/test_affstamp.py

Builds its own PDF fixtures, so it runs anywhere the tool runs.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import affstamp as A
import pdflinkcheck as plc
import pymupdf

FAILURES = []
MM = A.mm


def check(name, condition, detail=""):
    ok = bool(condition)
    print(("  ok   " if ok else "  FAIL ") + name +
          ("" if ok or not detail else "   <- %s" % (detail,)))
    if not ok:
        FAILURES.append(name)


def section(title):
    print("\n%s" % title)


def run(args):
    """Drive a command the way the menu does, swallowing its chatter."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = A._run(args)
    except SystemExit as exc:
        return (exc.code if isinstance(exc.code, int) else 1), buf.getvalue()
    return code, buf.getvalue()


# ---------------------------------------------------------------- fixtures

def make_base(path, pages=4, absolute=True):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=MM(210), height=MM(297))
        page.insert_text((MM(25), MM(40)), "AFFIDAVIT", fontsize=14)
        for line in range(14):
            page.insert_text((MM(25), MM(52 + line * 7)),
                             "%d. Exhibit DOC-%03d is produced."
                             % (line + 1, i * 14 + line + 1), fontsize=10)
        page.insert_text((MM(25), MM(276)), "DOC-%03d" % (i + 1), fontsize=9)
        rect = pymupdf.Rect(MM(25), MM(272), MM(60), MM(278))
        if absolute:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": rect,
                              "uri": "file:///C:/Nowhere/Exhibits/DOC-%03d.pdf"
                                     % (i + 1)})
        else:
            page.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": rect,
                              "file": "Exhibits/DOC-%03d.pdf" % (i + 1)})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_scan(path, pages=4, shadow_mm=3.0, ink=True, stray_mark=False):
    build = pymupdf.open()
    for i in range(pages):
        page = build.new_page(width=MM(209), height=MM(296))
        page.insert_text((MM(25), MM(40)), "AFFIDAVIT", fontsize=14)
        for line in range(14):
            page.insert_text((MM(25), MM(52 + line * 7)),
                             "%d. Exhibit DOC-%03d is produced."
                             % (line + 1, i * 14 + line + 1), fontsize=10)
        page.insert_text((MM(25), MM(276)), "DOC-%03d" % (i + 1), fontsize=9)
        if ink:
            x, y = MM(150), MM(284)
            for k in range(6):
                page.draw_bezier((x + k * MM(7), y),
                                 (x + k * MM(7) + MM(2), y - MM(8)),
                                 (x + k * MM(7) + MM(4), y + MM(5)),
                                 (x + k * MM(7) + MM(7), y - MM(3)),
                                 color=(0.05, 0.05, 0.05), width=2.0)
        if stray_mark and i == 1:
            # an initialled amendment half way up the page - the thing a
            # footer strip would silently miss
            page.draw_line((MM(150), MM(150)), (MM(185), MM(150)),
                           color=(0.05, 0.05, 0.05), width=2.5)
            page.draw_line((MM(150), MM(155)), (MM(180), MM(145)),
                           color=(0.05, 0.05, 0.05), width=2.5)
        if shadow_mm:
            page.draw_rect(pymupdf.Rect(0, page.rect.y1 - MM(shadow_mm),
                                        page.rect.x1, page.rect.y1),
                           color=(0.1, 0.1, 0.1), fill=(0.1, 0.1, 0.1))
    flat = pymupdf.open()
    for i in range(pages):
        pix = build[i].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
        page = flat.new_page(width=build[i].rect.width,
                             height=build[i].rect.height)
        page.insert_image(page.rect, pixmap=pix)
    flat.save(path, garbage=3, deflate=True)
    flat.close()
    build.close()


def make_exhibits(folder, names=None, miscase=None):
    ex = os.path.join(folder, "Exhibits")
    os.makedirs(ex, exist_ok=True)
    for name in (names or ["DOC-%03d.pdf" % i for i in range(1, 5)]):
        out = miscase if (miscase and name == "DOC-001.pdf") else name
        doc = pymupdf.open(); doc.new_page()
        doc.save(os.path.join(ex, out)); doc.close()


def actions(path):
    doc = pymupdf.open(path)
    out = []
    for page in doc:
        for link in plc.read_links(doc, page):
            out.append((link["action"], link["target"]))
    doc.close()
    return out


# ------------------------------------------------------------------- tests

def test_units():
    section("units and parsing")
    check("mm round trip", abs(A.pt(A.mm(25)) - 25) < 1e-9)
    check("pages 1-3", A.parse_pages("1-3", 10) == [0, 1, 2])
    check("pages clipped to length", A.parse_pages("1,2,40", 5) == [0, 1])
    check("blank means all", A.parse_pages("", 3) == [0, 1, 2])
    check("colour black", A.parse_colour("black") == (0, 0, 0))
    check("colour hex", A.parse_colour("#1A3FBB") == (26, 63, 187))
    lut = A.alpha_lut(205, 60)
    check("paper transparent", lut[255] == 0 and lut[205] == 0)
    check("ink opaque", lut[0] == 255 and lut[60] == 255)
    check("curve monotonic", all(lut[i] >= lut[i + 1] for i in range(255)))


def test_uri_to_path():
    section("absolute target detection")
    for target in ("file:///C:/x/y.pdf", "/C:/x/y.pdf", "C:/x/y.pdf",
                   "//server/share/y.pdf"):
        check("absolute: %s" % target, A.uri_to_path(target) is not None)
    for target in ("Exhibits/DOC-1.pdf", "./Exhibits/DOC-1.pdf",
                   "https://example.com/a.pdf", ""):
        check("not absolute: %r" % target, A.uri_to_path(target) is None)


def test_links_reads_true_action(work):
    section("links reads the action from the annotation, not get_links()")
    base = os.path.join(work, "mixed.pdf")
    doc = pymupdf.open()
    doc.new_page(width=MM(210), height=MM(297))
    page = doc[0]
    page.insert_link({"kind": pymupdf.LINK_LAUNCH,
                      "from": pymupdf.Rect(MM(20), MM(20), MM(80), MM(30)),
                      "file": "Exhibits/L.pdf"})
    page.insert_link({"kind": pymupdf.LINK_GOTOR,
                      "from": pymupdf.Rect(MM(20), MM(40), MM(80), MM(50)),
                      "file": "Exhibits/G.pdf", "page": 0,
                      "to": pymupdf.Point(0, 0)})
    doc.save(base); doc.close()

    got = dict((t, a) for a, t in actions(base))
    check("/Launch reported as /Launch", got.get("Exhibits/L.pdf") == "/Launch",
          got)
    check("/GoToR reported as /GoToR", got.get("Exhibits/G.pdf") == "/GoToR",
          got)

    d = pymupdf.open(base)
    kinds = {}
    for page in d:
        for lk in page.get_links():
            kinds[lk.get("file") or ""] = lk.get("kind")
    d.close()
    check("get_links() collapses both to GOTOR, which is why we do not use it",
          kinds.get("Exhibits/L.pdf") == pymupdf.LINK_GOTOR, kinds)

    code, out = run(["links", "--base", base, "--dump"])
    check("the dump shows /Launch", "/Launch" in out)


def test_links_policies(work):
    section("links --open-in")
    for mode in ("viewer", "browser", "any"):
        room = os.path.join(work, "pol_" + mode)
        os.makedirs(room, exist_ok=True)
        base = os.path.join(room, "b.pdf")
        make_base(base, absolute=False)
        # one absolute link so there is always something to repair
        doc = pymupdf.open(base)
        doc[0].insert_link({"kind": pymupdf.LINK_URI,
                            "from": pymupdf.Rect(MM(25), MM(250), MM(60), MM(256)),
                            "uri": "file:///C:/Nowhere/Exhibits/DOC-099.pdf"})
        doc.saveIncr() if False else doc.save(base + ".tmp")
        doc.close()
        os.replace(base + ".tmp", base)

        out = os.path.join(room, "fixed.pdf")
        code, _ = run(["links", "--base", base, "--fix-relative",
                       "--open-in", mode, "--out", out])
        check("%s: fix-relative succeeded" % mode, code == 0 and
              os.path.isfile(out))
        local = [(a, t) for a, t in actions(out)
                 if t and not t.startswith("http") and a != "/GoTo"]
        if mode == "viewer":
            check("viewer: all /GoToR",
                  all(a == "/GoToR" for a, _ in local), set(a for a, _ in local))
        elif mode == "browser":
            check("browser: all /URI",
                  all(a == "/URI" for a, _ in local), set(a for a, _ in local))
        else:
            kinds = dict((t, a) for a, t in local)
            check("any: /Launch stayed /Launch",
                  kinds.get("Exhibits/DOC-001.pdf") == "/Launch", kinds)
            check("any: the absolute /URI stayed /URI",
                  kinds.get("DOC-099.pdf") == "/URI", kinds)
        check("%s: no absolute targets remain" % mode,
              not any(A.uri_to_path(t) for _, t in local), local)
        check("%s: no leading ./" % mode,
              not any(t.startswith("./") for _, t in local), local)

    fitted = actions(os.path.join(work, "pol_viewer", "fixed.pdf"))
    doc = pymupdf.open(os.path.join(work, "pol_viewer", "fixed.pdf"))
    blobs = []
    for page in doc:
        for xref, atype, _ in page.annot_xrefs():
            if atype == pymupdf.PDF_ANNOT_LINK:
                blobs.append(doc.xref_object(xref, compressed=True))
    doc.close()
    check("destination is /Fit, not XYZ 0 0 0",
          all("/Fit" in b for b in blobs if "/GoToR" in b))


def test_links_check(work):
    section("links --check resolves targets, case included")
    room = os.path.join(work, "check")
    os.makedirs(room, exist_ok=True)
    base = os.path.join(room, "b.pdf")
    make_base(base, absolute=False)

    code, out = run(["links", "--base", base, "--check"])
    check("missing targets are reported", code == 1 and "do not exist" in out)

    make_exhibits(room)
    code, out = run(["links", "--base", base, "--check"])
    check("present targets pass", code == 0 and "resolve under" in out, out[-200:])

    os.rename(os.path.join(room, "Exhibits", "DOC-001.pdf"),
              os.path.join(room, "Exhibits", "doc-001.PDF"))
    code, out = run(["links", "--base", base, "--check"])
    check("a case mismatch is caught", code == 1 and "different capitals" in out)
    check("the real spelling is shown", "doc-001.PDF" in out)


def test_measure_and_stamp(work):
    section("measure, ruler, compare, stamp")
    room = os.path.join(work, "stamp")
    os.makedirs(room, exist_ok=True)
    base = os.path.join(room, "hyperlinked.pdf")
    scan = os.path.join(room, "scan.pdf")
    make_base(base, absolute=False)
    make_scan(scan)

    A.LAST_SUGGESTED.clear()
    code, out = run(["measure", "--scan", scan, "--base", base])
    check("measure ran", code == 0)
    check("measure suggested a height", A.LAST_SUGGESTED.get("height"),
          A.LAST_SUGGESTED)

    height = str(A.LAST_SUGGESTED.get("height") or 24)
    code, _ = run(["ruler", "--scan", scan, "--out-dir", room,
                   "--height", height, "--pages", "1-2"])
    check("ruler ran", code == 0)
    check("ruler wrote a file",
          any(f.endswith("_RULER.pdf") for f in os.listdir(room)))

    code, _ = run(["compare", "--base", base, "--scan", scan,
                   "--out-dir", room])
    check("compare ran", code == 0)
    check("ghost written", os.path.isfile(os.path.join(room, "GHOST.pdf")))

    out_pdf = os.path.join(room, "TEST.pdf")
    code, _ = run(["stamp", "--base", base, "--scan", scan, "--out", out_pdf,
                   "--height", height, "--pages", "1-2", "--skip-last"])
    check("trial stamp ran", code == 0 and os.path.isfile(out_pdf))

    final = os.path.join(room, "FINAL.pdf")
    code, out = run(["stamp", "--base", base, "--scan", scan, "--out", final,
                     "--height", height, "--replace-last"])
    check("full stamp ran", code == 0 and os.path.isfile(final))
    check("links preserved", "preserved" in out, out[-300:])

    original = open(base, "rb").read()
    check("base bytes are an intact prefix",
          open(final, "rb").read()[:len(original)] == original)

    src, dst = pymupdf.open(base), pymupdf.open(final)
    check("page count unchanged", src.page_count == dst.page_count)
    zone = pymupdf.Rect(MM(140), MM(272), MM(205), MM(292))
    dark = lambda d, i: sum(d[i].get_pixmap(dpi=72, clip=zone,
                                            colorspace=pymupdf.csGRAY).samples)
    check("ink landed in the strip zone", dark(dst, 0) < dark(src, 0))
    src.close(); dst.close()

    manifest = os.path.join(room, "FINAL_manifest.json")
    check("manifest written", os.path.isfile(manifest))
    if os.path.isfile(manifest):
        data = json.load(open(manifest))
        check("manifest is json with settings", isinstance(data, dict))


def test_audit(work):
    section("audit finds marks above the strip")
    room = os.path.join(work, "audit")
    os.makedirs(room, exist_ok=True)
    base = os.path.join(room, "b.pdf")
    clean = os.path.join(room, "clean_scan.pdf")
    marked = os.path.join(room, "marked_scan.pdf")
    make_base(base, absolute=False)
    make_scan(clean)
    make_scan(marked, stray_mark=True)

    code, out = run(["audit", "--base", base, "--scan", clean, "--band", "30"])
    check("a clean scan passes the audit", code == 0, out[-200:])

    code, out = run(["audit", "--base", base, "--scan", marked, "--band", "30"])
    check("an initialled amendment above the strip is caught", code == 1,
          out[-300:])
    check("it names the page", "p  2" in out or "p2" in out, out[-300:])


def test_guards(work):
    section("refusals")
    room = os.path.join(work, "guards")
    os.makedirs(room, exist_ok=True)
    base = os.path.join(room, "b.pdf")
    scan = os.path.join(room, "s.pdf")
    make_base(base, pages=3, absolute=False)
    make_scan(scan, pages=2)

    code, out = run(["stamp", "--base", base, "--scan", scan,
                     "--out", os.path.join(room, "o.pdf"), "--height", "20"])
    check("page count mismatch is refused", code != 0, out[-200:])

    code, _ = run(["links", "--base", os.path.join(room, "nope.pdf")])
    check("a missing file is refused", code != 0)

    code, _ = run(["links", "--base", base, "--fix-relative"])
    check("--fix-relative without --out is refused", code != 0)


def test_selftest():
    section("self-test")
    code, out = run(["selftest"])
    check("selftest passes", code == 0 and "SELFTEST PASSED" in out,
          out[-200:])


def main():
    print("AffStamp %s regression tests" % A.VERSION)
    work = tempfile.mkdtemp(prefix="affstamp_tests_")
    try:
        test_units()
        test_uri_to_path()
        test_links_reads_true_action(work)
        test_links_policies(work)
        test_links_check(work)
        test_measure_and_stamp(work)
        test_audit(work)
        test_guards(work)
        test_selftest()
    finally:
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
