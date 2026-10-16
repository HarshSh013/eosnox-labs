@echo off
title Eosnox Labs - Install
rem Runs the installer script next to this file. Nothing is changed outside the user's own folders
rem except one Windows Firewall rule, which Windows asks permission for.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0files\install.ps1"
echo.
pause
