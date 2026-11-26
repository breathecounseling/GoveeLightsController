import sys

if sys.version_info < (3, 10):
    sys.exit(f"goveelights needs Python 3.10 or newer; this is {sys.version.split()[0]} ({sys.executable}).\n"
             "Install a newer Python (python.org, or Homebrew on a Mac) and run it with that one.")

from .cli import main  # noqa: E402

sys.exit(main())
