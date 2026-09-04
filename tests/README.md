# Tests

```
py tests/test_affstamp.py      # engine: links, measure, stamp, guard rails
py tests/test_gui.py           # the window, driven headlessly
```

Both build their own PDF fixtures and clean up after themselves. Nothing else
is needed — no test framework, no sample documents.

`_stub_tk/` is a stand-in for tkinter so `test_gui.py` can construct the window
and call its callbacks without a display. It proves the wiring (threading, log
capture, the fields Measure fills in, the guard rails, the confirmation
dialog, saved state). It says nothing about how the window looks — run
`run.bat` for that.
