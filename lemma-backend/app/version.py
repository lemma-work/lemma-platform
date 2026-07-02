"""Single source of truth for the public API (OpenAPI) version.

This value is surfaced as the FastAPI ``version`` on every app variant
(``app.app:create_app``, ``standalone_app``, ``app.scheduler``) and therefore
as ``info.version`` in ``GET /openapi.json``. The generated ``lemma-sdk``
records the version it was built against (``lemma_sdk._spec_info``), and the
``lemma`` CLI (``lemma --version`` / ``lemma doctor``) compares the two to flag
client/server skew.

Bump this on ANY change to the public OpenAPI schema (new/removed endpoints,
request/response shape changes). The SDK regeneration script
(``lemma-python/scripts/generate_openapi_client.sh``) refuses to commit a schema
change that does not also bump this version, so skew can no longer be
introduced silently.

Use a normal MAJOR.MINOR.PATCH string.
"""

from __future__ import annotations

API_VERSION = "3.3.0"
