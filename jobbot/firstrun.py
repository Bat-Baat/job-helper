"""One-time setup: downloads the browser Job Helper uses to fill in applications."""
import subprocess
import sys

from . import store

MARKER = store.DATA_DIR / ".browser-installed"

if not MARKER.exists():
    print("First-time setup: downloading the browser Job Helper uses (about 150 MB, one time only)…")
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
    if r.returncode == 0:
        MARKER.write_text("ok")
    else:
        print("Couldn't download the browser. Check your internet connection and try again.")
        sys.exit(1)
