"""What a person does with Lemma Desktop, done for real against a real install.

These paths differ from the server build -- the host pack's environment, the VZ
guest, locald's ports, WKWebView -- and every one of them has already shipped a
bug that the server-side suites could not see:

* every pod app rendered signed out, because WebKit will not send the session
  cookie across two `.localhost` hosts;
* every function died at ``getaddrinfo``, because sandboxes were handed an
  address only the Mac could resolve.

Both were found by a person using the app, not by CI. This is the suite that
would have caught them.

Run with ``make desktop-e2e``.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from lemma_sdk.errors import LemmaTimeoutError

pytestmark = [pytest.mark.desktop_e2e, pytest.mark.asyncio]

REPO_ROOT = Path(__file__).resolve().parents[4]
PROBE = REPO_ROOT / "desktop" / "e2e" / "pod_app_session_probe.swift"

# A minimal build of the shape Vite emits: an entrypoint that pulls one asset
# by root-absolute path. That path is the reason apps are served at the root of
# their own subdomain, so a build that did not have one would not exercise the
# arrangement being tested.
APP_INDEX = """<!doctype html>
<html><head><meta charset="utf-8"><title>E2E app</title>
<script type="module" crossorigin src="/assets/app.js"></script>
<link rel="stylesheet" crossorigin href="/assets/app.css">
</head><body><div id="root"></div></body></html>
"""
# How long a freshly written file may take to become searchable. Indexing is
# a background job; this is the point past which "not yet" is a real failure.
INDEXING_PATIENCE_SECONDS = 120

APP_JS = "window.__E2E_APP_LOADED = true;\n"
APP_CSS = "#root { color: rebeccapurple; }\n"

# Deliberately arithmetic and not an LLM call: this lane is about whether a
# function reaches the guest, runs, and gets its answer back -- not about model
# behaviour. Anything non-deterministic here would make a real dispatch failure
# indistinguishable from a flaky model.
FUNCTION_CODE = """# input_type_name: AddInput
# output_type_name: AddResult
# function_name: {name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext


class AddInput(BaseModel):
    a: int
    b: int


class AddResult(BaseModel):
    total: int


async def {name}(ctx: FunctionContext, data: AddInput) -> AddResult:
    return AddResult(total=data.a + data.b)
"""


def _dist_archive() -> Path:
    """Zip the little app above into what ``upload_bundle`` expects."""
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    with zipfile.ZipFile(handle, "w") as archive:
        archive.writestr("index.html", APP_INDEX)
        archive.writestr("assets/app.js", APP_JS)
        archive.writestr("assets/app.css", APP_CSS)
    handle.close()
    return Path(handle.name)


@pytest.fixture(scope="module")
def client(install, account):
    """The Python SDK, pointed at the install and holding a real session.

    A raw sign-up is not put through onboarding, so it may own no organisation
    yet. One is made here rather than assumed, because "list()[0]" against an
    empty list is an IndexError three fixtures away from the thing that is
    actually wrong.
    """
    from lemma_sdk import Lemma

    lemma = Lemma(base_url=install.api_url, token=account.access_token)
    existing = lemma.orgs.list().items
    org = existing[0] if existing else lemma.orgs.create(name=f"e2e-{os.getpid()}")
    lemma.org_id = str(org.id)
    return lemma


@pytest.fixture(scope="module")
def pod(client):
    """A pod of this run's own, removed afterwards.

    Torn down even when a test fails: a harness that leaves debris behind stops
    being runnable twice, and "it passed the second time after I cleaned up" is
    how a real failure gets waved through.
    """
    from lemma_sdk.openapi_client.models import PodCreateRequest

    created = client.pods.create(
        PodCreateRequest(
            name=f"e2e-{os.getpid()}",
            organization_id=client.org_id,
            description="desktop e2e",
        )
    )
    pod_id = str(created.id)
    try:
        yield client.pod(pod_id)
    finally:
        try:
            client.pods.delete(pod_id)
        except Exception as error:  # noqa: BLE001 - teardown must not mask a failure
            print(f"warning: could not remove the e2e pod {pod_id}: {error}")


@pytest.fixture(scope="module")
def published_app(pod, install):
    """A published app, served by host, exactly as a user's app is."""
    from lemma_sdk.openapi_client.models import CreateAppRequest

    slug = f"e2e-app-{os.getpid()}"
    pod.apps.create(CreateAppRequest(name=slug, public_slug=slug))
    archive = _dist_archive()
    try:
        pod.apps.upload_bundle(slug, dist_archive=archive)
    finally:
        archive.unlink(missing_ok=True)
    return slug


# --- app serving ---------------------------------------------------------------


async def test_a_published_app_serves_every_asset_it_asks_for(published_app, install):
    """The entrypoint *and* what it references.

    Fetching only `/` is what made this look healthy while it was broken: the
    live install returned 200 for index.html and then 404ed five asset requests
    in a row, which is a blank page with a green healthcheck behind it.
    """
    base = install.app_url(published_app)
    index = httpx.get(base, timeout=30)
    assert index.status_code == 200, f"the app's entrypoint 404ed: {index.text[:200]}"
    assert "E2E app" in index.text

    # The host injects this; an app with no config cannot reach the API at all.
    assert "__LEMMA_CONFIG__" in index.text

    for asset in ("assets/app.js", "assets/app.css"):
        response = httpx.get(base + asset, timeout=30)
        assert response.status_code == 200, (
            f"{asset} is referenced by the entrypoint and 404ed, which is a "
            f"blank app: {response.text[:200]}"
        )


async def test_the_app_is_told_to_call_the_api_on_its_own_origin(
    published_app, install
):
    """A relative apiUrl is the whole fix, so it is asserted directly.

    An absolute one pointing at the API host is the shipped bug: to WebKit that
    is a different site, the session cookie is third-party, and the app renders
    signed out.
    """
    index = httpx.get(install.app_url(published_app), timeout=30).text
    marker = "window.__LEMMA_CONFIG__="
    start = index.index(marker) + len(marker)
    config = json.loads(index[start : index.index("</script>", start)].rstrip(";"))

    assert config["apiUrl"].startswith("/"), (
        f"the app was handed an absolute apiUrl ({config['apiUrl']!r}); its "
        "calls will be cross-site and carry no session"
    )
    # And that prefix has to actually route.
    probe = httpx.get(
        install.app_url(published_app).rstrip("/") + config["apiUrl"] + "/users/me",
        timeout=30,
    )
    assert probe.status_code in (200, 401), (
        f"the app's own API prefix does not route: HTTP {probe.status_code}. "
        "401 is fine here (no cookie on this client); 404 means the middleware "
        "is not stripping it."
    )


async def test_a_pod_app_is_signed_in_in_the_engine_lemma_ships(
    published_app, install, account
):
    """The regression test for apps loading signed out.

    Driven through **WKWebView**, which is not a detail: Chromium sends the
    cookie in exactly this arrangement, so a Playwright or httpx test passes
    against the broken build. This is the only lane that can tell the two apart.
    """
    assert PROBE.is_file(), f"the WKWebView probe is missing at {PROBE}"

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as config_file:
        json.dump(
            {
                "frontendUrl": install.frontend_url,
                "apiUrl": install.api_url,
                "appUrl": install.app_url(published_app),
                "email": account.email,
                "password": account.password,
            },
            config_file,
        )
        config_path = config_file.name

    try:
        result = subprocess.run(
            ["swift", str(PROBE), config_path],
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        Path(config_path).unlink(missing_ok=True)

    report = (result.stdout or "").strip()
    assert result.returncode == 0, (
        "a pod app loaded without a session in WKWebView — the engine the "
        f"desktop app actually ships.\n{report}\n{result.stderr[-2000:]}"
    )
    answer = json.loads(report)
    assert answer["status"] == 200
    assert answer["email"] == account.email


# --- functions -----------------------------------------------------------------


async def test_a_function_runs_in_the_guest_and_returns_its_result(pod, install):
    """Guards the sandbox's address as much as the function itself.

    Functions had no gateway URL at all on desktop, so the dispatcher fell back
    to an address that resolves only on the Mac and every run died at
    ``getaddrinfo`` — surfaced to the user as ``FUNCTION_VALIDATION_ERROR``,
    which reads as their code being wrong.
    """
    if not install.provisions_sandboxes:
        pytest.skip(
            "this stack has no locald, so nothing dispatches a function into a "
            "guest sandbox. Run `make desktop-e2e` against a packaged install "
            "for this lane."
        )

    from lemma_sdk.openapi_client.models import (
        CreateFunctionRequest,
        FunctionRunStatus,
    )

    name = f"e2e_add_{os.getpid()}"
    pod.functions.create(
        CreateFunctionRequest(
            name=name,
            code=FUNCTION_CODE.format(name=name),
            description="desktop e2e",
        )
    )
    try:
        run = pod.functions.run(name, {"a": 2, "b": 3})
        assert run.status == FunctionRunStatus.COMPLETED, (
            f"the function did not complete: {run.status} "
            f"error={run.error!r} logs={str(run.logs)[-500:]!r}"
        )
        assert run.output_data["total"] == 5
    finally:
        try:
            pod.functions.delete(name)
        except Exception as error:  # noqa: BLE001
            print(f"warning: could not remove the e2e function {name}: {error}")


# --- pod files -----------------------------------------------------------------


async def test_a_file_survives_upload_search_and_download(pod):
    """Upload, find it, read it back, byte for byte.

    Desktop stores these on local disk through the host pack's
    ``LOCAL_FILE_STORAGE_ROOT`` rather than in object storage, so the round
    trip is a different code path from the server build's.
    """
    body = "the quick brown fox jumps over the lazy dog\n" * 32
    path = f"/e2e-{os.getpid()}.txt"

    pod.files.write_text(path, body)
    try:
        # Download first: it is synchronous, so a failure here is storage and
        # not indexing, and separating the two is the difference between "the
        # desktop file path is broken" and "the indexer has not caught up".
        downloaded = pod.files.download(path)
        assert downloaded.decode() == body, "the file did not survive the round trip"

        # Indexing is a background job, so this polls rather than asserting on
        # the first answer. A bare assert here fails on a working install and
        # teaches everyone to rerun the suite until it is green, which is how a
        # real regression gets waved through.
        deadline = time.monotonic() + INDEXING_PATIENCE_SECONDS
        names: list[str] = []
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                names = [
                    item.path for item in pod.files.search("quick brown fox").items
                ]
            except LemmaTimeoutError as error:
                # The first search on a cold stack can exceed the SDK's own
                # timeout while the index warms up. That is the condition this
                # loop exists to wait out, so it is not a result -- retrying
                # until the deadline is. A genuinely broken search still fails,
                # because `names` never comes to contain the file.
                last_error = error
                continue
            if path in names:
                break
            time.sleep(2)
        assert path in names, (
            f"{path} never became searchable within {INDEXING_PATIENCE_SECONDS}s; "
            f"last answer: {names[:10]}"
            + (f"; last error: {last_error}" if last_error else "")
        )
    finally:
        try:
            pod.files.delete(path)
        except Exception as error:  # noqa: BLE001
            print(f"warning: could not remove the e2e file {path}: {error}")


async def test_a_binary_file_is_not_mangled(pod):
    """Text round-tripping proves less than it looks: an encoding bug in the
    desktop storage path shows up first on bytes that are not valid UTF-8."""
    blob = bytes(range(256)) * 8
    path = f"/e2e-{os.getpid()}.bin"

    pod.files.upload_file(
        io.BytesIO(blob), path=path, filename=path.lstrip("/"), search_enabled=False
    )
    try:
        assert pod.files.download(path) == blob
    finally:
        try:
            pod.files.delete(path)
        except Exception as error:  # noqa: BLE001
            print(f"warning: could not remove the e2e file {path}: {error}")
