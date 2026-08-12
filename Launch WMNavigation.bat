@echo off
cd /d "%~dp0"
if exist "WMNavigation.exe" (
    start "" "WMNavigation.exe"
) else if exist "dist\WMNavigation.exe" (
    start "" "dist\WMNavigation.exe"
) else (
    python -m wmnavi
)
