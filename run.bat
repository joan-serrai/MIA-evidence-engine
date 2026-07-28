@echo off
REM run.bat - Doble clic para arrancar MIA en Windows.
REM Llama a run.ps1 saltando la politica de ejecucion (solo para este proceso).
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0run.ps1"
if errorlevel 1 pause
