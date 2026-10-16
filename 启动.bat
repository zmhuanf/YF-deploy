@echo off
rem YF Deploy launcher, double click to run; pause on error so the window does not flash away
cd /d "%~dp0"
python app.py
if errorlevel 1 pause
