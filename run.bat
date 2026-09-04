@echo off
rem Opens the AffStamp window from the source .py files (developer use).
rem The packaged build is AffStamp\AffStamp.exe - just double-click that.
cd /d "%~dp0"
py "%~dp0affstamp_gui.py" %*
if errorlevel 1 pause
