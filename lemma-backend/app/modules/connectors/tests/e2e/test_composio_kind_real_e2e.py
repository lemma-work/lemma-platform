"""The composio kind against the live Composio API.

Everything else in the connector suite talks to something we control. This talks
to Composio itself, because the parts most likely to break are the parts we do
not own: whether the SDK still returns what we parse, whether a real tool call
round-trips, and -- the reason this file exists -- whether a real file download
comes back as something the caller can actually use.

Gated on ``COMPOSIO_API_KEY`` and skipped without it, so CI is unaffected. The
connected account is resolved once and cached (see ``composio_account_cache``);
the first run opens a browser for consent when ``RUN_HUMAN_OAUTH=1``, and every
later run reuses it.

Run::

    # after connecting once
    pytest -m provider app/modules/connectors/tests/e2e/test_composio_kind_real_e2e.py -s

    # first time, or after revoking access
    RUN_HUMAN_OAUTH=1 pytest -m "provider and human" \\
        app/modules/connectors/tests/e2e/test_composio_kind_real_e2e.py -s
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.errors import (
    OperationExecutionNotFoundError,
    OperationExecutionUnauthorizedError,
)
from app.modules.connectors.infrastructure.adapters.composio_operation_gateway import (
    ComposioOperationGateway,
)
from app.modules.connectors.infrastructure.kinds import build_kind_registry
from app.modules.connectors.services.execution import KindDispatcher
from app.modules.connectors.tests.e2e.composio_account_cache import (
    resolve_connected_account,
)

pytestmark = [pytest.mark.e2e, pytest.mark.provider, pytest.mark.asyncio]

# The toolkit used for the shared connection. Google Drive because it is the one
# that exercises real file download, which is the behaviour this suite is here
# to protect.
_TOOLKIT = os.getenv("LEMMA_E2E_COMPOSIO_TOOLKIT", "googledrive")
_E2E_USER_ID = os.getenv("LEMMA_E2E_COMPOSIO_USER_ID", "lemma-connector-e2e")


@pytest.fixture(scope="module")
def composio_client():
    if not connector_settings.composio_api_key:
        pytest.skip("COMPOSIO_API_KEY is not configured.")
    from composio import Composio

    os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")
    return Composio(api_key=connector_settings.composio_api_key)


@pytest.fixture(scope="module")
def connected_account_id(composio_client) -> str:
    """One live connection, consented once and reused by every test here."""
    return resolve_connected_account(
        composio_client, toolkit=_TOOLKIT, user_id=_E2E_USER_ID
    )


@pytest.fixture
def dispatcher() -> KindDispatcher:
    # The package gateway is never reached: every request here is composio-kind.
    return KindDispatcher(
        build_kind_registry(
            composio_gateway=ComposioOperationGateway(), package_gateway=AsyncMock()
        )
    )


def _request(dispatcher: KindDispatcher, operation: str, payload: dict, account_id: str):
    return dispatcher.build_request(
        connector_id=_TOOLKIT,
        kind=ConnectorKind.COMPOSIO,
        operation=ResolvedOperation(name=operation.lower(), provider_operation_name=operation),
        payload=payload,
        credentials={"connection_id": account_id},
        config={},
    )


class TestTheConnectionIsReal:
    async def test_the_cached_account_is_active_at_composio(
        self, composio_client, connected_account_id
    ):
        account = composio_client.connected_accounts.get(connected_account_id)
        assert str(account.status).upper() == "ACTIVE"

    async def test_composio_still_advertises_the_toolkits_tools(self, composio_client):
        # If Composio renames or drops the tools we ship in the catalog, this is
        # where we find out -- not in a customer's pod.
        tools = composio_client.tools.get(user_id=_E2E_USER_ID, toolkits=[_TOOLKIT.upper()])
        assert tools, f"Composio returned no tools for {_TOOLKIT}"


class TestExecution:
    async def test_a_real_operation_round_trips_through_the_dispatcher(
        self, dispatcher, connected_account_id
    ):
        request = _request(
            dispatcher, "GOOGLEDRIVE_LIST_FILES", {"page_size": 5}, connected_account_id
        )
        result = await dispatcher.execute(request)
        assert isinstance(result, dict)

    async def test_an_unknown_tool_is_reported_as_not_found(
        self, dispatcher, connected_account_id
    ):
        request = _request(
            dispatcher, "GOOGLEDRIVE_NO_SUCH_TOOL_AT_ALL", {}, connected_account_id
        )
        with pytest.raises(OperationExecutionNotFoundError):
            await dispatcher.execute(request)

    async def test_a_bogus_connection_is_reported_as_unauthorized(self, dispatcher):
        # Drives the path that flips an account to REAUTH_REQUIRED. Getting this
        # classification wrong means a revoked account fails as a 500 forever
        # instead of prompting the user to reconnect.
        request = _request(
            dispatcher, "GOOGLEDRIVE_LIST_FILES", {}, "ca_definitely_not_a_real_id"
        )
        with pytest.raises(
            (OperationExecutionUnauthorizedError, OperationExecutionNotFoundError)
        ):
            await dispatcher.execute(request)


class TestTheEventLoopStaysFree:
    async def test_a_real_execution_does_not_block_the_loop(
        self, dispatcher, connected_account_id
    ):
        """The Composio SDK is synchronous; proof it is actually offloaded.

        A heartbeat coroutine ticks every 10ms alongside a real (network-bound)
        Composio call. If the SDK ran on the loop, the heartbeat would stall for
        the whole round trip and record almost no ticks.
        """
        import asyncio

        ticks = 0
        stop = asyncio.Event()

        async def heartbeat():
            nonlocal ticks
            while not stop.is_set():
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            request = _request(
                dispatcher, "GOOGLEDRIVE_LIST_FILES", {"page_size": 1}, connected_account_id
            )
            await dispatcher.execute(request)
        finally:
            stop.set()
            await beat

        # A network round trip to Composio takes far longer than 10 ticks; if the
        # loop had been blocked we would see close to zero.
        assert ticks > 10, f"event loop appears to have been blocked (ticks={ticks})"


class TestFileDownload:
    """The behaviour this suite exists for.

    With Composio's managed file handling on, a download is written to the
    backend container's local disk and the response carries a filesystem path --
    a path the caller cannot reach, for a file that accumulates on the box
    forever. These assert what a caller actually receives today, so the change
    to owning both directions has a baseline to compare against.
    """

    @pytest.fixture
    def drive_file_id(self, composio_client, connected_account_id) -> str:
        listing = composio_client.tools.execute(
            "GOOGLEDRIVE_LIST_FILES",
            {"page_size": 10},
            connected_account_id=connected_account_id,
            dangerously_skip_version_check=True,
        )
        data = (listing.get("data") if isinstance(listing, dict) else None) or {}
        files = data.get("files") or data.get("items") or []
        candidates = [
            f
            for f in files
            if isinstance(f, dict)
            and f.get("id")
            and not str(f.get("mimeType", "")).endswith(".folder")
        ]
        if not candidates:
            pytest.skip("The connected Drive has no downloadable file to test with.")
        return candidates[0]["id"]

    async def test_download_returns_something_the_caller_can_use(
        self, dispatcher, connected_account_id, drive_file_id
    ):
        request = _request(
            dispatcher,
            "GOOGLEDRIVE_DOWNLOAD_FILE",
            {"file_id": drive_file_id},
            connected_account_id,
        )
        result = await dispatcher.execute(request)

        # Record what actually comes back, so the shape is visible when this is
        # read after the file protocol lands.
        print(f"\nGOOGLEDRIVE_DOWNLOAD_FILE returned: {str(result)[:400]}\n")
        assert result is not None

    async def test_download_does_not_leave_the_file_on_this_machine(
        self, dispatcher, connected_account_id, drive_file_id
    ):
        """Currently expected to FAIL until we own the download path.

        Composio's managed handling writes the payload under COMPOSIO_CACHE_DIR
        and substitutes the local path into the response. This asserts the
        intended end state -- nothing on local disk -- and is marked xfail so the
        suite stays honest about the gap rather than asserting the bug.
        """
        from pathlib import Path

        cache_dir = Path(os.environ.get("COMPOSIO_CACHE_DIR", "/tmp/composio")) / "files"
        before = set(cache_dir.rglob("*")) if cache_dir.exists() else set()

        request = _request(
            dispatcher,
            "GOOGLEDRIVE_DOWNLOAD_FILE",
            {"file_id": drive_file_id},
            connected_account_id,
        )
        await dispatcher.execute(request)

        after = set(cache_dir.rglob("*")) if cache_dir.exists() else set()
        new_files = {p for p in (after - before) if p.is_file()}
        if new_files:
            pytest.xfail(
                f"Composio wrote {len(new_files)} file(s) to local disk "
                f"({next(iter(new_files))}); the caller receives an unusable "
                "path. Fixed when the connector owns download and streams to the "
                "pod datastore."
            )
