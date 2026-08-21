"""
startup_health.py
==================
Tiny CLI shim so run.bat can run the Telemetry health check exactly once,
at the very first moment the app launches -- before the mode menu is even
shown. Kept separate from main.py/plot.py (which used to each run their own
health check) because run.bat can end up invoking either or both of them
in a single session, which printed "Startup health check: OK" more than
once and buried it after the mode banner instead of showing it first.

Usage:
  py src/lib/event/startup_health.py
"""

import os
import sys

# Allow running this script directly (py .../startup_health.py) -- put
# <project_root>/src on sys.path so the lib.event.* absolute import resolves.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from lib.event import telemetry

if __name__ == "__main__":
    telemetry.log_startup_health()
