#!/usr/bin/env python3
"""CLI entrypoint for `python3 scripts/lint_brands.py` delegating to `shared/tools/lint_brands.py`."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "shared" / "tools" / "lint_brands.py"
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")
