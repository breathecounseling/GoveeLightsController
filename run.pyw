"""Windowless launcher (pythonw run.pyw [serve ...]) that works from any working
directory: used by the at-boot scheduled task."""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
os.chdir(here)
from goveelights.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:] or ["serve", "--log", os.path.join(here, "goveelights.log")]))
