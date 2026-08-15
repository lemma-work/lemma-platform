"""One Composio SDK client per process, per configuration.

``Composio(...)`` is not a cheap handle. It reads configuration, builds an httpx
client and imports the SDK's lazy namespaces; measured in a production pod it
takes **42-262 ms**, and it is synchronous, so all of that lands on the event
loop of whoever constructs it.

Four places built one. One of them — the webhook verifier — had already noticed
and cached it, with a comment measuring the same cost at a neighbouring call
site. The other three had not: a per-request auth provider, a per-request
operation gateway, and a schedule manager that built one on every trigger
create and delete. This module is that fix applied once instead of four times,
and it is what the ``process-lifetime-construction`` gate points at.

Keyed on the arguments that actually change behaviour rather than cached as a
single slot: the managed-files flag alters what the SDK does with responses, so
two clients that disagree about it are genuinely different clients. In practice
that is one entry, occasionally two.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.modules.connectors.config import connector_settings


@lru_cache(maxsize=4)
def _build(api_key: str | None, allow_managed_files: bool) -> Any:
    # Set before the SDK is imported: it reads this at import time, and without
    # it the SDK picks a cache directory under the home of whatever user the
    # container runs as, which may not be writable.
    os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")
    from composio import Composio

    return Composio(
        api_key=api_key,
        dangerously_allow_auto_upload_download_files=allow_managed_files,
    )


def get_composio_client(*, allow_managed_files: bool = False) -> Any:
    """The shared Composio client for this configuration.

    ``allow_managed_files`` governs both upload and download, and the download
    half is unusable in this deployment: the SDK writes the payload to the
    container's local disk and substitutes the local path into the response, so
    the caller receives a path it cannot open for a file that accumulates on the
    box forever. Callers that want the upload half pass signed URLs themselves
    and stream Composio's ``{name, mimetype, s3url}`` envelope to the pod
    datastore.
    """
    return _build(connector_settings.composio_api_key, allow_managed_files)


def reset_composio_clients() -> None:
    """Drop the cached clients. For tests that monkeypatch the API key."""
    _build.cache_clear()
