#!/usr/bin/env python3
"""Root launcher for MP3 to ProTracker MOD converter (ZX Spectrum General Sound).

Version: 1.1.0
"""

import sys
import os
import io

# Ensure stdout/stderr exist in windowed (--noconsole) mode on Windows
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# Add directory containing this script to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.version import __version__
from src.main import main

if __name__ == "__main__":
    sys.exit(main())
