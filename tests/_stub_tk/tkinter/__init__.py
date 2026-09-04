"""Minimal stand-in for tkinter so the GUI can be driven headlessly."""
CALLBACKS = []          # (delay, fn) queued by after()
DIALOGS = []            # every messagebox that was raised

class TclError(Exception):
    pass

class _Var:
    def __init__(self, master=None, value=None):
        self._v = value
    def get(self):
        return self._v
    def set(self, v):
        self._v = v

class StringVar(_Var):
    def __init__(self, master=None, value=""):
        super().__init__(master, "" if value is None else value)

class BooleanVar(_Var):
    def __init__(self, master=None, value=False):
        super().__init__(master, bool(value))

class _Widget:
    def __init__(self, master=None, **kw):
        self.master = master
        self.kw = dict(kw)
        self.children = []
        self._state = []
        if isinstance(master, _Widget):
            master.children.append(self)
    def pack(self, **kw): return self
    def grid(self, **kw): return self
    def place(self, **kw): return self
    def configure(self, **kw): self.kw.update(kw); return self
    config = configure
    def cget(self, k): return self.kw.get(k)
    def columnconfigure(self, *a, **k): pass
    def rowconfigure(self, *a, **k): pass
    def state(self, spec=None):
        if spec is None: return tuple(self._state)
        self._state = list(spec); return tuple(self._state)
    def xview_moveto(self, *a): pass
    def yview(self, *a): pass
    def bind(self, *a, **k): pass
    def destroy(self): pass
    def winfo_exists(self): return True

class Text(_Widget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.lines = []
        self.tags = {}
    def insert(self, index, text, tag=None):
        self.lines.append((text, tag))
    def see(self, *a): pass
    def delete(self, *a): self.lines = []
    def get(self, *a): return "".join(t for t, _ in self.lines)
    def tag_configure(self, name, **kw): self.tags[name] = kw

class Frame(_Widget): pass
class Label(_Widget): pass
class Entry(_Widget): pass
class Button(_Widget): pass
class Checkbutton(_Widget): pass
class Scrollbar(_Widget):
    def set(self, *a): pass

class _TkCall:
    def call(self, *a, **k): return ""

class Tk(_Widget):
    def __init__(self, *a, **k):
        super().__init__(None)
        self.tk = _TkCall()
        self.destroyed = False
    def title(self, *a): pass
    def minsize(self, *a): pass
    def geometry(self, *a): pass
    def protocol(self, *a): pass
    def after(self, delay, fn=None, *args):
        if fn is not None:
            CALLBACKS.append((delay, lambda: fn(*args)))
        return "id"
    def after_cancel(self, *a): pass
    def mainloop(self): pass
    def destroy(self): self.destroyed = True
    def update(self): pass

def pump(rounds=400):
    """Run queued after() callbacks (the 80ms _drain keeps re-arming)."""
    for _ in range(rounds):
        if not CALLBACKS:
            return
        delay, fn = CALLBACKS.pop(0)
        fn()
