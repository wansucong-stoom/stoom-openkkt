@echo off
start "" "%~dp0.venv\Scripts\pythonw.exe" -m openkkt.cli --config "%~dp0config.local.json" gui
