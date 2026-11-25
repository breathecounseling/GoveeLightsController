@echo off
REM Starts the Govee lights creator app. Close this window or press Ctrl+C to stop
REM (the lights are released back to the Govee app on exit).
cd /d "%~dp0"
python -m goveelights %*
pause
