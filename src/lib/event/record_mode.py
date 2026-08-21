"""
record_mode.py
===============
Tiny CLI shim so run.bat can report which top-level mode the user picked
("1) Process ZIP/GUCAL files" or "2) Use existing result files") to the
Telemetry API. Kept separate from main.py because Mode 2 skips main.py
entirely (it jumps straight to plot.py), so main.py isn't a reliable place
to capture the selection for both branches.

Usage:
  py src/lib/event/record_mode.py 1
  py src/lib/event/record_mode.py 2
"""

import os
import sys

# Allow running this script directly (py .../record_mode.py) -- put
# <project_root>/src on sys.path so the lib.event.* absolute import resolves.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from lib.event import telemetry

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    telemetry.log_feature_click(f"SelectMode_{mode}")
