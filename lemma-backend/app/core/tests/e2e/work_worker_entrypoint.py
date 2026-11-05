"""``python -m app.worker`` plus the work kinds the worker-process test needs."""

import app.core.tests.e2e.work_worker_handlers  # noqa: F401  registers the work kinds
from app.worker import main

if __name__ == "__main__":
    main()
