from tkinter import _Widget, Frame, Label, Entry, Button, Checkbutton, Scrollbar
class LabelFrame(_Widget): pass
class Progressbar(_Widget):
    def start(self, *a): self.running = True
    def stop(self, *a): self.running = False
class Style(_Widget):
    def __init__(self, *a, **k): super().__init__(None)
    def configure(self, *a, **kw): self.kw.update(kw); return self
    def theme_names(self): return ("clam", "alt", "default")
    def theme_use(self, name=None): return "clam"
