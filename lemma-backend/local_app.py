"""Managed-local Lemma application entrypoint.

The two-process desktop/backend topology: one Python process owns the API,
Streaq worker, scheduler and sandbox provisioning, while the Next frontend is
the only other Lemma application process. Infrastructure and sandbox compute
remain outside this process.
"""

from __future__ import annotations

# First, and before the application package is imported. Not a warm-up: see
# `load_extension_modules` for what happens when this import runs later, which
# on Windows is that the process never serves anything at all. Measured there:
# from here it costs 0.8s; from four lines below, after `app.app`, it does not
# finish.
from app.core.embeddings.local_embedder import load_extension_modules_if_local

load_extension_modules_if_local()

from fastapi import FastAPI  # noqa: E402

from app.core.locald_watchdog import install_locald_parent_watchdog  # noqa: E402
from app.app import create_app as create_api_app  # noqa: E402
from app.standalone import build_standalone_app  # noqa: E402


def create_local_app() -> FastAPI:
    from app.events import streaq_worker

    return build_standalone_app(create_api_app(), streaq_worker)


install_locald_parent_watchdog()
app = create_local_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "local_app:app",
        host="0.0.0.0",
        port=8711,
        reload=False,
        ws="websockets-sansio",
        access_log=False,
    )
