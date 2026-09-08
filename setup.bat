@echo off
REM setup.bat - Doble clic para la instalacion guiada de MIA (primera vez).
REM Llama a setup.ps1 saltando la politica de ejecucion (solo para este proceso).
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0setup.ps1"
if errorlevel 1 pause
