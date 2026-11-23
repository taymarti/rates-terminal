@echo off
rem Double-click to start the rates terminal (see start.ps1 for options).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
