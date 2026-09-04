PDFLinkCheck 1.0.0
==================

Checks that the hyperlinks in a PDF are RELATIVE, and that they open in the
PDF viewer rather than in a web browser. Can rewrite the ones that are not.

No installation, no Python, no admin rights. 64-bit Windows 10 or 11.


WHICH FILE DO I RUN?
--------------------

PDFLinkCheck\PDFLinkCheck.exe     <- use this one normally
PDFLinkCheck-single.exe           <- one self-contained file, if you want to
                                     copy just one thing around

They do exactly the same job. The folder version starts faster and is safer
on locked-down machines: the single file unpacks itself into your temp folder
each time it runs, which some corporate security policies block. If the single
file will not start, use the folder version.

Do NOT separate PDFLinkCheck.exe from the _internal folder beside it.


HOW TO USE IT
-------------

Easiest: drag a PDF straight onto PDFLinkCheck.exe.

Or double-click it and paste in a path when it asks. It will offer to fix
anything it finds.

Or from a command prompt:

    PDFLinkCheck.exe bundle.pdf              check one file
    PDFLinkCheck.exe C:\Affidavit            check every PDF in a folder
    PDFLinkCheck.exe bundle.pdf --fix        rewrite -> bundle_fixed.pdf

The fix never overwrites your file. It writes a new one alongside it, named
<name>_fixed.pdf. Always re-run the check on the fixed file.


WHAT IT IS LOOKING FOR
----------------------

1. RELATIVE PATHS. A link to "Exhibits\DOC-001.pdf" travels with the bundle.
   A link to "C:\Users\chris\Desktop\Exhibits\DOC-001.pdf" works only on the
   machine it was made on. Word writes the absolute kind whenever the
   Hyperlink Base is not set.

2. THE RIGHT ACTION TYPE. A PDF link can carry one of several actions, and
   only one of them guarantees the exhibit opens in the PDF viewer:

     /GoToR    opens the target in the same PDF viewer.  <- what you want
     /Launch   hands the file to Windows, so it opens in whatever owns .pdf
               on that machine - Edge or Chrome on most modern builds.
               Acrobat also warns that the file "may contain programs,
               macros, or viruses" before it will follow the link.
     /URI      always goes to the browser. Wrong for a local exhibit.

   --fix rewrites both of the bad ones as relative /GoToR.


READING THE OUTPUT
------------------

    FAIL   must be fixed before the bundle is sent anywhere
    warn   look at it and decide - a link to legislation on the web is a
           perfectly legitimate browser link, so those are only flagged
    ok     relative, and opens in the PDF viewer

The last line is the verdict for the file. The program also sets an exit
code - 0 if everything passed, 1 if anything failed - so it can be used as
a gate in a batch file:

    PDFLinkCheck.exe final.pdf --no-pause || echo DO NOT SEND THIS

Use --no-pause whenever you call it from a script, otherwise it waits for
a keypress at the end.


ONE THING TO KNOW
-----------------

Fixing links changes the PDF. If the file has been certified or digitally
signed, rewriting the links will invalidate that signature. Fix the links
first, then certify - never the other way round.
