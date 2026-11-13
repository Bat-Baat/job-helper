"""Starting a browser. Uses the Chromium that Playwright downloaded on first run."""
from __future__ import annotations

import os


def launch_kwargs() -> dict:
    path = os.environ.get("JOBHELPER_CHROMIUM")  # only needed for development setups
    return {"executable_path": path} if path else {}
