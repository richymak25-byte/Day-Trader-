@echo off
setlocal
title MINDLOCK Journal - installation
cd /d "%~dp0..\.."
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY (
  echo Python 3 est introuvable sur ce PC.
  echo Installe-le depuis python.org en cochant "Add python.exe to PATH", puis relance ce fichier.
  start "" https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
echo [1/4] Python :
%PY% --version
echo.
echo [2/4] Installation du module MetaTrader5 (lecture de l'historique MT5)...
%PY% -m pip install --upgrade --user MetaTrader5
echo.
if not exist "trader\journal" mkdir "trader\journal"
if not exist "trader\journal\connectors.json" (
  copy /y "mindlock-journal\connectors.prop-firms.example.json" "trader\journal\connectors.json" >nul
  echo [3/4] Configuration creee : trader\journal\connectors.json
) else (
  echo [3/4] Configuration existante conservee : trader\journal\connectors.json
)
echo.
echo [4/4] Terminaux MT5 trouves sur ce PC (garde-les ouverts sur tes comptes) :
%PY% mindlock-journal\sync.py --list-mt5
echo.
choice /c ON /m "Lancer MINDLOCK automatiquement au demarrage de Windows"
if errorlevel 2 goto done
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\MINDLOCK Journal.lnk'); $s.TargetPath='%CD%\mindlock-journal\windows\Lancer MINDLOCK.bat'; $s.Arguments='silent'; $s.WorkingDirectory='%CD%'; $s.WindowStyle=7; $s.Save()"
echo Demarrage automatique active (raccourci dans le dossier Demarrage de Windows).
:done
echo.
echo Derniere etape : complete Topstep (identifiant + cle API) et Lucid (Tradovate) dans le fichier qui s'ouvre,
echo enregistre-le, puis double-clique "Lancer MINDLOCK.bat".
start "" notepad "trader\journal\connectors.json"
pause
