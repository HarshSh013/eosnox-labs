@echo off
title Eosnox Labs - Uninstall
if exist "%LOCALAPPDATA%\Programs\Eosnox Labs\uninstall.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Programs\Eosnox Labs\uninstall.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0files\uninstall.ps1"
)
