@echo off
title MINDLOCK Journal
cd /d "%~dp0..\.."
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY (
  echo Lance d'abord "Installer MINDLOCK.bat".
  pause
  exit /b 1
)
set "OPEN=--open"
if /i "%~1"=="silent" set "OPEN="
echo MINDLOCK Journal : http://127.0.0.1:8765
echo Tes comptes sont relus toutes les 60 secondes. Ferme cette fenetre pour arreter.
echo.
%PY% mindlock-journal\sync.py --serve %OPEN%
pause
