from __future__ import annotations

import sys

__all__ = ["__version__"]

__version__ = "0.5.5"

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
