"""Use-case e2e: a bundled app is created, deployed, and served on import.

A static (no-``package.json``) app exercises the whole APP apply path — create the
app with a unique slug, upload its source + dist, finalize a release — without an
sandbox build, proving apps travel with their code and actually serve in the
target pod. (The Vite build-in-sandbox path is covered by unit tests with a mocked
session and, under ``E2E_REAL``, a real build.)
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    import_and_apply,
    pack_fixture_bundle,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def test_static_app_imports_and_serves(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_app_static")

    await import_and_apply(authenticated_client, pod_id, zip_bytes)

    # The app exists and is fully deployed (READY, with a current release) — proving
    # create + source/dist upload + release finalize all ran on import.
    got = await authenticated_client.get(f"/pods/{pod_id}/apps/dashboard")
    assert got.status_code == status.HTTP_200_OK, got.text
    body = got.json()
    assert body["status"] == "READY", body
    assert body["current_release_id"], body
    assert body["public_slug"], body
    # The app's source travelled with the bundle and was stored for re-builds.
    assert body["source_archive_path"], body

    # And it serves the index.html the bundle shipped.
    served = await authenticated_client.get(f"/pods/{pod_id}/apps/dashboard/assets")
    assert served.status_code == status.HTTP_200_OK, served.text
    assert "Imported Dashboard" in served.text
