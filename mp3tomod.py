#!/usr/bin/env python3
"""Root launcher for MP3 to ProTracker MOD converter."""

import sys
import os

# Add directory containing this script to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    sys.exit(main())
