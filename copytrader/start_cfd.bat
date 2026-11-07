@echo off
cd /d "%~dp0"
python -m copytrade cfd %*
pause
