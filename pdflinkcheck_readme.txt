PDFLinkCheck 1.4.0
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

Or double-click it and paste in a path when it asks. After the report it
asks whether to repair or change the links, and if you say yes:

    How should the links open?
      1) in the PDF viewer          (recommended)
      2) in the web browser
      3) leave the link types alone - only fix paths and capitals

    Should an exhibit open in its own window?
      1) yes - never close the submission  (recommended)
      2) no - replace the submission with the exhibit
      3) leave it to each reader's own settings

Press Enter to take the recommended answer. It then shows the report for
the fixed file, so you can see what you ended up with.

Or from a command prompt:

    PDFLinkCheck.exe bundle.pdf              check one file
    PDFLinkCheck.exe C:\Affidavit            check every PDF in a folder
    PDFLinkCheck.exe bundle.pdf --fix        repair -> bundle_fixed.pdf

    PDFLinkCheck.exe bundle.pdf --fix --open-in browser
    PDFLinkCheck.exe bundle.pdf --fix --open-in viewer --window new

The fix never overwrites your file. It writes a new one alongside it, named
<name>_fixed.pdf.


WHAT --fix DOES TO EACH LINK
----------------------------

  - converts it to the link type you chose
  - makes an absolute path relative
  - FINDS THE FILE: Word records the path on the author's machine, such as
    C:\Users\someone\Matter\Documents\DOC-005.pdf. The fixer follows that
    path's own folders into this bundle - Matter\Documents\DOC-005.pdf,
    then Documents\DOC-005.pdf - and uses the first that exists. It never
    goes looking for any file of the same name, so a superseded copy in an
    Archive folder will not be picked up by accident. It lists every file it
    found this way so you can confirm they are the right documents.
  - corrects the capitals to match the file on disk
  - pins the window setting you chose

What it cannot do is supply a document that is not there. Anything still
missing afterwards is listed by name at the end - put the file in place,
or correct the link in the Word document and export again.


WHAT IT IS LOOKING FOR
----------------------

0. THAT THE TARGET IS ACTUALLY THERE. A link can be perfectly formed and
   still not open, because nothing of that name is sitting where it points.
   The tool resolves every relative link against the folder the PDF is in
   and reports:

     TARGET NOT FOUND   nothing of that name is there
     CASE MISMATCH      it is there, but spelled with different capitals

   Case matters more than it looks. Windows ignores capitalisation, and so
   does a normal Mac disk - but a case-sensitive volume, a network share,
   a SharePoint or iManage library, and Linux do not. A bundle that works
   perfectly on the machine that built it can fail everywhere else for this
   reason alone, and the error you get is just "cannot open the file".

   Use --no-resolve to skip this, if you are checking a PDF away from its
   exhibits.

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


3. WHICHEVER BEHAVIOUR YOU HAVE CHOSEN. Use --open-in to say how the links
   in this bundle are meant to behave. The tool then checks against that
   choice, and --fix converts to it.

     --open-in viewer    (the default) every local link must be /GoToR, so
                         exhibits open in the PDF viewer. Best for a bundle
                         read in Acrobat.

     --open-in browser   every local link must be /URI, so exhibits are
                         handed to the default browser. Worth considering if
                         your recipients are on Macs or tablets, where the
                         browser is more reliable than the PDF viewer.

     --open-in any       leave the action types exactly as they are, and
                         only check and repair the paths. Use this when the
                         bundle already behaves the way you want and you
                         only care that the links are relative and resolve.

   Examples:

       PDFLinkCheck.exe bundle.pdf --open-in browser
       PDFLinkCheck.exe bundle.pdf --fix --open-in browser
       PDFLinkCheck.exe bundle.pdf --fix --open-in any

   A bundle built for one policy will fail a check under another, which is
   the point - it tells you the bundle is not what you thought it was.


4. THAT FOLLOWING A LINK DOES NOT CLOSE WHAT YOU ARE READING.

   A PDF link can say whether the exhibit opens in its OWN window, or in
   the window you are already in - which closes the submission and replaces
   it with the exhibit. That setting is called /NewWindow, and if it is not
   set, the reader decides: Acrobat follows Preferences > Documents > "open
   cross-document links in same window", which differs from desk to desk.

   That is why the same bundle behaves one way on your machine and another
   way on a colleague's, and why some links in a bundle behave differently
   from others - the ones Word wrote may carry the setting while the ones
   added later do not.

   The tool pins it, so no reader has to guess:

     --window new    (the default) the exhibit always opens in its own
                     window; following a link never closes the submission
     --window same   always replace the current document
     --window any    do not check or change it

   --fix pins it on every file link, including ones it did not otherwise
   need to touch.


READING THE OUTPUT
------------------

Every link gets one row:

  STATUS PAGE  OPENS IN      WINDOW          PATH      FILE        TARGET
  ok     p1    PDF viewer    new window      relative  found       Documents/DOC-001.pdf
  FAIL   p1    PDF viewer    SAME window     relative  found       Documents/DOC-002.pdf
  FAIL   p1    default app   reader decides  relative  found       Documents/DOC-003.pdf
  FAIL   p1    browser       -               relative  MISSING     Documents/DOC-004.pdf
  FAIL   p1    browser       -               ABSOLUTE  -           file:///C:/Users/...
  FAIL   p2    PDF viewer    reader decides  relative  wrong case  Documents/DOC-006.pdf

  OPENS IN   PDF viewer      /GoToR - opens in Acrobat or whichever PDF
                             program is reading the bundle
             browser         /URI - handed to the web browser
             default app     /Launch - handed to Windows, which opens it in
                             whatever program owns .pdf on that machine
             this document   a jump to another page of the same PDF

  WINDOW     new window      always opens beside the submission
             SAME window     always closes the submission and replaces it
             reader decides  not set - depends on each person's Acrobat
                             settings, so it varies from desk to desk
             -               does not apply (browser and internal links)

  PATH       relative / ABSOLUTE / web

  FILE       found / MISSING / wrong case, checked against the folder the
             PDF is in. "-" where it does not apply.

Below the table, each problem is explained in a sentence.

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


IF THE LINKS FAIL ON A MAC
--------------------------

Symptom: clicking a link says it cannot open the file, or mentions
permissions - but opening that same file by hand works perfectly.

Run this tool on the bundle first. If it reports TARGET NOT FOUND or CASE
MISMATCH, that is your answer and it is fixable. If it says PASS, the file
is fine and the problem is on the Mac. In rough order of likelihood:

1. QUARANTINE. If the bundle arrived by download, email, AirDrop or a USB
   stick, macOS tags every file with a quarantine flag, and Acrobat will
   not follow a link into a quarantined file. Opening by hand still works,
   which is exactly the symptom. In Terminal:

       xattr -dr com.apple.quarantine /path/to/the/bundle/folder

2. ICLOUD DRIVE. If the folder is in iCloud Drive with "Optimise Mac
   Storage" on, the exhibit may not actually be on the disk - only a
   placeholder. Opening by hand downloads it; following a link does not.
   Move the bundle to a real local folder, or right-click the folder and
   choose "Download Now".

3. FILES AND FOLDERS PERMISSION. System Settings > Privacy & Security >
   Files and Folders, and give Acrobat access to the folder the bundle is
   in (Desktop, Documents and Downloads each need granting separately).
   Opening by hand goes through the file dialog, which grants access for
   that one file - which is why manual opening works and links do not.

4. ENHANCED SECURITY IN ACROBAT. Acrobat > Settings > Security (Enhanced).
   Either add the bundle folder under "Add File Path" in Privileged
   Locations, or untick "Enable Enhanced Security" while you work with it.

5. PREVIEW INSTEAD OF ACROBAT. Apple's Preview does not properly support
   links from one PDF to another. If the recipient opens the bundle in
   Preview, the links will not work no matter how the file is built. Set
   Acrobat as the default PDF application, or tell the recipient to open
   the bundle in Acrobat.

Only the first two are things you can fix from your side before sending.
Worth doing both before the bundle goes anywhere.


ONE THING TO KNOW
-----------------

Fixing links changes the PDF. If the file has been certified or digitally
signed, rewriting the links will invalidate that signature. Fix the links
first, then certify - never the other way round.
