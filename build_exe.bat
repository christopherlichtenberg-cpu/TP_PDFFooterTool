@echo off
rem ---------------------------------------------------------------------
rem Rebuilds dist\AffStamp\ - both executables - from AffStamp.spec.
rem
rem   AffStamp.exe       windowed GUI (double-click this)
rem   AffStamp-cli.exe   console: the menu, and every command-line option
rem
rem Build on the SAME Windows bitness as the target machine.
rem ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"
py -m pip install --upgrade -r requirements.txt pyinstaller || goto :fail
py -m PyInstaller --noconfirm --clean AffStamp.spec || goto :fail
rem 6.4 MB of C++ headers shipped with pymupdf; not used at runtime.
rmdir /s /q dist\AffStamp\_internal\pymupdf\mupdf-devel 2>nul
copy /y RUNBOOK.md dist\AffStamp\ >nul
copy /y "START HERE.txt" dist\AffStamp\ >nul
echo.
echo Built dist\AffStamp\AffStamp.exe  and  AffStamp-cli.exe
echo Verify on the TARGET machine with:  AffStamp-cli.exe selftest
echo.
certutil -hashfile dist\AffStamp\AffStamp.exe SHA256
goto :eof
:fail
echo BUILD FAILED
exit /b 1
