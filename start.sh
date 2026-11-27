#!/bin/sh
# Starts the Govee lights app in this terminal (Linux, Raspberry Pi, macOS): the same as
# double-clicking start.bat on Windows. Ctrl+C stops it and hands the lights back to the
# Govee app. Extra words are passed on, e.g.  ./start.sh --port 8090   or   ./start.sh diag
# To start it at boot instead, run once:  python3 -m goveelights install-service
cd "$(dirname "$0")" || exit 1
if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
else
    PY=python3
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Python 3.10 or newer is needed ($PY is missing or older). On a Raspberry Pi: sudo apt install python3" >&2
    exit 1
fi
exec "$PY" -m goveelights "$@"
