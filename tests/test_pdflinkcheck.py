#!/usr/bin/env python3
"""
Tests for pdflinkcheck.  Self-contained - needs only PyMuPDF, no Pillow, so it
runs in the slim build job.

    py tests/test_pdflinkcheck.py
"""

import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdflinkcheck as P
import pymupdf as fitz

FAILURES = []
MM = 72 / 25.4


def check(name, condition, detail=""):
    ok = bool(condition)
    print(("  ok   " if ok else "  FAIL ") + name +
          ("" if ok or not detail else "   <- %s" % (detail,)))
    if not ok:
        FAILURES.append(name)


def section(title):
    print("\n%s" % title)


def make_bundle(path):
    """One PDF carrying every link type we care about."""
    doc = fitz.open()
    doc.new_page(width=210 * MM, height=297 * MM)
    doc.new_page(width=210 * MM, height=297 * MM)
    p1, p2 = doc[0], doc[1]
    box = lambda y: fitz.Rect(25 * MM, y * MM, 120 * MM, (y + 6) * MM)

    p1.insert_link({"kind": fitz.LINK_URI, "from": box(40),
                    "uri": "file:///C:/Users/someone/Bundle/Ex/DOC-001.pdf"})
    p1.insert_link({"kind": fitz.LINK_LAUNCH, "from": box(50),
                    "file": "Exhibits/DOC-002.pdf"})
    p1.insert_link({"kind": fitz.LINK_GOTOR, "from": box(60),
                    "file": "Exhibits/DOC-003.pdf", "page": 0,
                    "to": fitz.Point(0, 0)})
    p1.insert_link({"kind": fitz.LINK_GOTO, "from": box(70), "page": 1,
                    "to": fitz.Point(0, 0)})
    p1.insert_link({"kind": fitz.LINK_URI, "from": box(80),
                    "uri": "https://www.legislation.gov.au/"})
    p1.insert_link({"kind": fitz.LINK_LAUNCH, "from": box(90),
                    "file": "//fileserver/legal/DOC-006.pdf"})
    for i, y in enumerate((40, 50, 60)):
        p2.insert_link({"kind": fitz.LINK_LAUNCH, "from": box(y),
                        "file": "Exhibits/DOC-%03d.pdf" % (10 + i)})
    doc.save(path, garbage=3, deflate=True)
    doc.close()


def make_exhibits(work, miscase=False, skip=()):
    """The files the fixed bundle's links point at."""
    ex = os.path.join(work, "Exhibits")
    os.makedirs(ex, exist_ok=True)
    beside = ["DOC-001.pdf", "DOC-006.pdf"]
    inside = ["DOC-002.pdf", "DOC-003.pdf", "DOC-010.pdf", "DOC-011.pdf",
              "DOC-012.pdf"]
    for name in beside:
        if name in skip:
            continue
        doc = fitz.open(); doc.new_page()
        doc.save(os.path.join(work, name)); doc.close()
    for name in inside:
        if name in skip:
            continue
        out = name
        if miscase and name == "DOC-012.pdf":
            out = "doc-012.PDF"
        doc = fitz.open(); doc.new_page()
        doc.save(os.path.join(ex, out)); doc.close()


def raw_actions(path):
    doc = fitz.open(path)
    out = []
    for page in doc:
        for link in P.read_links(doc, page):
            out.append((link["action"], link["target"]))
    doc.close()
    return out


def test_paths():
    section("path rewriting")
    cases = [
        # already relative: must be left exactly as it is
        ("Exhibits/DOC-001.pdf", "/bundle", "Exhibits/DOC-001.pdf"),
        ("Exhibits\\DOC-001.pdf", "/bundle", "Exhibits/DOC-001.pdf"),
        ("DOC-001.pdf", "/bundle", "DOC-001.pdf"),
        ("../Shared/DOC-001.pdf", "/bundle", "../Shared/DOC-001.pdf"),
        # absolute: rewritten relative to the bundle where that makes sense
        ("file:///C:/Affidavit/Ex/DOC-001.pdf", "C:/Affidavit", "Ex/DOC-001.pdf"),
        ("C:/Affidavit/Ex/Sub/D.pdf", "C:/Affidavit", "Ex/Sub/D.pdf"),
        ("file:///C:/Affidavit/Ex/DOC%20A.pdf", "C:/Affidavit", "Ex/DOC A.pdf"),
        # absolute but from somewhere else: the file name is all that is left
        ("C:/Users/someone/Other/D.pdf", "C:/Affidavit", "D.pdf"),
        ("D:/Elsewhere/D.pdf", "C:/Affidavit", "D.pdf"),
        ("//server/share/D.pdf", "C:/Affidavit", "D.pdf"),
    ]
    for target, anchor, want in cases:
        got = P.relative_to(target, anchor)
        check("%-42s -> %s" % (target, want), got == want, got)


def test_absolute_detection():
    section("absolute detection")
    for target in ("C:\\x\\y.pdf", "/C:/x/y.pdf", "file:///C:/x.pdf",
                   "//server/share/x.pdf", "\\\\server\\share\\x.pdf",
                   "/usr/share/x.pdf"):
        check("absolute: %s" % target, P.is_absolute(target))
    for target in ("Exhibits/DOC-1.pdf", "DOC-1.pdf", "../up/DOC-1.pdf"):
        check("relative: %s" % target, not P.is_absolute(target))


def test_raw_action_reading(work):
    section("action is read from the annotation, not get_links()")
    bundle = os.path.join(work, "bundle.pdf")
    make_bundle(bundle)

    actions = dict((t, a) for a, t in raw_actions(bundle))
    check("/Launch is seen as /Launch",
          actions.get("Exhibits/DOC-002.pdf") == "/Launch",
          actions.get("Exhibits/DOC-002.pdf"))
    check("/GoToR is seen as /GoToR",
          actions.get("Exhibits/DOC-003.pdf") == "/GoToR",
          actions.get("Exhibits/DOC-003.pdf"))

    # The regression this tool exists for: PyMuPDF collapses both to kind 5,
    # so anything built on get_links() cannot tell them apart.
    doc = fitz.open(bundle)
    kinds = {}
    for page in doc:
        for link in page.get_links():
            kinds[link.get("file") or link.get("uri") or ""] = link.get("kind")
    doc.close()
    check("get_links() really does report /Launch as GOTOR (so we cannot use it)",
          kinds.get("Exhibits/DOC-002.pdf") == fitz.LINK_GOTOR,
          kinds.get("Exhibits/DOC-002.pdf"))


def test_verdicts(work):
    section("verdicts")
    bundle = os.path.join(work, "bundle.pdf")
    doc = fitz.open(bundle)
    verdicts = {}
    for page in doc:
        for link in P.read_links(doc, page):
            verdicts[link["target"]] = P.classify(link)[0]
    doc.close()

    check("absolute /URI fails",
          verdicts.get("file:///C:/Users/someone/Bundle/Ex/DOC-001.pdf") == "fail")
    check("relative /Launch fails", verdicts.get("Exhibits/DOC-002.pdf") == "fail")
    check("relative /GoToR passes", verdicts.get("Exhibits/DOC-003.pdf") == "ok")
    check("UNC /Launch fails", verdicts.get("//fileserver/legal/DOC-006.pdf") == "fail")
    check("web link is a warning, not a failure",
          verdicts.get("https://www.legislation.gov.au/") == "warn")


def test_cli(work):
    section("command line")
    bundle = os.path.join(work, "bundle.pdf")
    check("failing bundle exits 1", P.main([bundle]) == 1)
    check("resolution can be turned off",
          P.main([bundle, "--no-resolve"]) == 1)
    check("--allow-launch still fails the absolute and UNC paths",
          P.main([bundle, "--allow-launch"]) == 1)

    empty = os.path.join(work, "nolinks.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.save(empty)
    doc.close()
    check("a PDF with no links exits 1", P.main([empty]) == 1)
    check("a missing file exits 1", P.main([os.path.join(work, "nope.pdf")]) == 1)
    check("a folder is walked", P.main([work]) == 1)


def test_fix(work):
    section("--fix")
    bundle = os.path.join(work, "bundle.pdf")
    P.main([bundle, "--fix"])
    fixed = os.path.join(work, "bundle_fixed.pdf")
    check("wrote _fixed.pdf", os.path.isfile(fixed))

    actions = raw_actions(fixed)
    targets = [t for a, t in actions]
    check("every file link is now /GoToR",
          all(a in ("/GoToR", "/GoTo", "/URI") for a, t in actions),
          set(a for a, _ in actions))
    check("the subfolder survived on a link that was already relative",
          "Exhibits/DOC-002.pdf" in targets, targets)
    check("page 2 links kept their subfolder too",
          "Exhibits/DOC-010.pdf" in targets)
    check("the genuine /GoToR was left alone",
          "Exhibits/DOC-003.pdf" in targets)
    check("the absolute path fell back to the file name",
          "DOC-001.pdf" in targets, targets)
    check("the UNC path fell back to the file name",
          "DOC-006.pdf" in targets)
    check("the web link was not touched",
          "https://www.legislation.gov.au/" in targets)
    check("destination is /Fit, not XYZ 0 0 0 (which opens at the page foot)",
          all("/Fit" in a for a in goto_r_actions(fixed)), goto_r_actions(fixed))

    check("without the exhibits present, every target is reported missing",
          P.main([fixed]) == 1)
    make_exhibits(work)
    check("with the exhibits present, the fixed file passes",
          P.main([fixed]) == 0)


def goto_r_actions(path):
    doc = fitz.open(path)
    out = []
    for page in doc:
        for xref, atype, _ in page.annot_xrefs():
            if atype != fitz.PDF_ANNOT_LINK:
                continue
            blob = doc.xref_object(xref, compressed=True)
            if "/GoToR" in blob:
                out.append(blob)
    doc.close()
    return out


def test_resolution(work):
    section("does the target actually exist?")
    room = os.path.join(work, "resolve")
    os.makedirs(os.path.join(room, "Exhibits"), exist_ok=True)
    doc = fitz.open(); doc.new_page()
    doc.save(os.path.join(room, "Exhibits", "DOC-001.pdf")); doc.close()

    check("exact match resolves",
          P.resolve_target("Exhibits/DOC-001.pdf", room)[0] == "found")
    check("wrong case is caught even on a case-insensitive filesystem",
          P.resolve_target("exhibits/doc-001.PDF", room)[0] == "case",
          P.resolve_target("exhibits/doc-001.PDF", room))
    check("the real spelling is reported back",
          P.resolve_target("exhibits/doc-001.PDF", room)[1]
          == "Exhibits/DOC-001.pdf",
          P.resolve_target("exhibits/doc-001.PDF", room)[1])
    check("a missing file is caught",
          P.resolve_target("Exhibits/DOC-999.pdf", room)[0] == "missing")
    check("a missing folder is caught",
          P.resolve_target("Nope/DOC-001.pdf", room)[0] == "missing")
    check("leading ./ still resolves",
          P.resolve_target("./Exhibits/DOC-001.pdf", room)[0] == "found")
    check("web links are skipped",
          P.resolve_target("https://example.com/a.pdf", room)[0] == "skip")
    check("absolute paths are skipped",
          P.resolve_target("C:/x/y.pdf", room)[0] == "skip")

    # a bundle whose only fault is a miscased target must fail
    bundle = os.path.join(room, "b.pdf")
    doc = fitz.open(); doc.new_page(width=200, height=200)
    doc[0].insert_link({"kind": fitz.LINK_GOTOR,
                        "from": fitz.Rect(10, 10, 100, 30),
                        "file": "exhibits/DOC-001.pdf", "page": 0,
                        "to": fitz.Point(0, 0)})
    doc.save(bundle); doc.close()
    check("a well-formed link to a miscased file still fails",
          P.main([bundle]) == 1)
    check("--no-resolve lets it pass", P.main([bundle, "--no-resolve"]) == 0)


def main():
    print("PDFLinkCheck %s tests" % P.VERSION)
    work = tempfile.mkdtemp(prefix="pdflinkcheck_tests_")
    try:
        test_paths()
        test_absolute_detection()
        test_raw_action_reading(work)
        test_verdicts(work)
        test_cli(work)
        test_fix(work)
        test_resolution(work)
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
