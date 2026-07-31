from __future__ import annotations

import os


if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:
        # Coverage must never prevent app/test subprocess startup.
        pass
