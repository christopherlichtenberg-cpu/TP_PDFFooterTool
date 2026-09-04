import tkinter
ANSWER = True
def askyesno(title=None, message=None, **kw):
    tkinter.DIALOGS.append(("askyesno", message)); return ANSWER
def showerror(title=None, message=None, **kw):
    tkinter.DIALOGS.append(("error", message)); return "ok"
def showinfo(title=None, message=None, **kw):
    tkinter.DIALOGS.append(("info", message)); return "ok"
def showwarning(title=None, message=None, **kw):
    tkinter.DIALOGS.append(("warning", message)); return "ok"
