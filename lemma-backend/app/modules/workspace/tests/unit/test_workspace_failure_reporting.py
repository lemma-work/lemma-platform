"""What a broken workspace tells the caller.

Every test here is about the *shape* of a failure rather than the failure
itself. The desktop bug these were written for was not that a sandbox broke --
it was that a broken sandbox was indistinguishable from a slow one, so a file
listing span five minutes and then returned `500 INTERNAL_ERROR` with a null
message. Three separate defects had to line up for that, and each has a test.

The bridge is a real subprocess and the runtime is a real HTTP server, for the
reason the neighbouring module gives: framing, exit codes and status codes are
the thing under test, so mocking them away would certify nothing.
"""

from __future__ import annotations

import stat
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from sandbox_runtime.errors import SandboxRejected, SandboxUnavailable

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderInstance, ProviderRejected
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.lemma_local import (
    LemmaLocalProviderConfig,
    LemmaLocalSandboxProvider,
)
from app.modules.workspace.providers.runtime_client import (
    WorkspaceRuntimeClient,
)
from app.modules.workspace.providers.runtime_errors import (
    WorkspaceRuntimeUnauthorized,
)

pytestmark = pytest.mark.asyncio

PINNED = "lemma-workspace@sha256:" + "c" * 64


class _FixedStatusRuntime:
    """A workspace runtime that answers every request with one status code."""

    def __init__(self, status_code: int) -> None:
        code = status_code

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"invalid runtime credential"}')

            do_POST = do_GET
            do_PUT = do_GET

            def log_message(self, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


_BRIDGE = """
import json, os, sys
runtime_url = os.environ["RUNTIME_URL"]
request = json.loads(sys.stdin.read())
op = request["operation"]
apps = {"runtime": {"port": 8080, "private_url": runtime_url}}
status = {"state": "running", "runtime_url": runtime_url, "apps": apps}
if op in ("sandbox.ensure", "sandbox.status"):
    print(json.dumps({"ok": True, "result": {
        "status": status, "provider_id": request["parameters"]["sandbox_id"]}}))
    sys.exit(0)
print(json.dumps({"ok": True, "result": {}}))
"""

_COUNTING_BRIDGE = """
import json, os, sys
request = json.loads(sys.stdin.read())
op = request["operation"]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(op + "\\n")
runtime_url = os.environ["RUNTIME_URL"]
apps = {"runtime": {"port": 8080, "private_url": runtime_url}}
status = {"state": "running", "runtime_url": runtime_url, "apps": apps}
print(json.dumps({"ok": True, "result": {
    "status": status, "provider_id": request["parameters"].get("sandbox_id")}}))
"""

_HANGING_BRIDGE = """
import time
time.sleep(30)
"""


def _bridge(tmp_path: Path, script: str) -> Path:
    path = tmp_path / "lemma-bridge"
    path.write_text("#!/usr/bin/env python3\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    return path


def _deadline(seconds: float = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _instance(sandbox_id) -> ProviderInstance:
    guest_id = "w-" + sandbox_id.hex
    return ProviderInstance(
        provider_id=guest_id,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        running=True,
    )


async def test_a_refused_credential_is_definitive_not_a_reason_to_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """401 must raise `SandboxRejected`, which callers do not retry.

    It used to fall through `_status_error`'s default to a bare
    `WorkspaceRuntimeError`, which both `_ops` scopes turn into
    `SandboxUnavailable` -- the retryable word. Callers then retried a rejected
    credential until their deadline and reported a timeout.
    """
    runtime = _FixedStatusRuntime(401)
    try:
        monkeypatch.setenv("RUNTIME_URL", runtime.url)
        provider = LemmaLocalSandboxProvider(
            LemmaLocalProviderConfig(executable=str(_bridge(tmp_path, _BRIDGE))),
            RuntimeCredentialSigner(key=b"k" * 32),
        )
        sandbox_id = uuid4()
        with pytest.raises(SandboxRejected):
            await provider.create_directory(
                _instance(sandbox_id),
                path="/workspace/somewhere",
                deadline_at=_deadline(),
            )
    finally:
        runtime.close()


async def test_a_bridge_that_never_answers_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bridge timeout must arrive as a sandbox error carrying a sentence.

    Uncaught it left the provider as a bare `asyncio.TimeoutError`, which every
    caller rendered as `500 INTERNAL_ERROR` with `error_message: None`.
    """
    monkeypatch.setenv("RUNTIME_URL", "http://127.0.0.1:1")
    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(
            executable=str(_bridge(tmp_path, _HANGING_BRIDGE)),
            request_timeout_seconds=0.5,
        ),
        RuntimeCredentialSigner(key=b"k" * 32),
    )
    with pytest.raises(SandboxUnavailable) as caught:
        await provider.create_directory(
            _instance(uuid4()), path="/workspace/x", deadline_at=_deadline(2)
        )
    assert str(caught.value), "the failure must carry a reason, not an empty message"


@pytest.mark.asyncio(loop_scope=None)
async def test_an_unauthorized_status_is_recognised_for_every_endpoint() -> None:
    """Not just the filesystem calls.

    `_status_error` consults a per-call table, and only the filesystem calls
    pass one. A credential refusal is the same event on every endpoint, so it
    is recognised before the table is consulted.
    """
    for code in (401, 403):
        error = WorkspaceRuntimeClient._status_error(code, None)
        assert isinstance(error, WorkspaceRuntimeUnauthorized), code
        assert error.status_code == code


async def test_a_second_operation_does_not_ask_the_guest_where_the_runtime_is_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The address of a container does not change between two file writes.

    Every workspace operation entered `_ops`, and `_ops` asked the guest for a
    status snapshot before doing anything: a `hostctl` fork on the host, a vsock
    round-trip, and a `nerdctl inspect` fork inside the VM. `_ops` has seventeen
    call sites, so a multi-step file operation paid all of that once per step.

    Driven against a runtime that actually answers, because a failing operation
    drops the remembered address on purpose -- an endpoint that stopped working
    is exactly the one not to keep.
    """
    runtime = _FixedStatusRuntime(204)
    calls = tmp_path / "calls.log"
    try:
        monkeypatch.setenv("CALL_LOG", str(calls))
        monkeypatch.setenv("RUNTIME_URL", runtime.url)
        provider = LemmaLocalSandboxProvider(
            LemmaLocalProviderConfig(
                executable=str(_bridge(tmp_path, _COUNTING_BRIDGE))
            ),
            RuntimeCredentialSigner(key=b"k" * 32),
        )
        instance = _instance(uuid4())

        for _ in range(3):
            await provider.create_directory(
                instance, path="/workspace/x", deadline_at=_deadline()
            )
    finally:
        runtime.close()

    recorded = calls.read_text().split()
    assert recorded.count("sandbox.status") == 1, (
        "each operation asked the guest where the runtime is: " + " ".join(recorded)
    )


async def test_a_bridge_that_cannot_be_started_is_not_a_missing_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release must not report success because the bridge is missing.

    `LocalBridgeNotFound` means "the guest says that sandbox does not exist":
    `_status` turns it into `ProviderGone`, and `_mutate` treats it as the
    outcome already achieved and returns. So classifying a bridge that cannot
    be spawned as *not found* would have reported a release, a delete and a
    storage purge as done while none of them happened.
    """
    # Present at construction -- which is checked -- and not runnable when it
    # is finally spawned. An upgrade that swaps the binary mid-flight looks
    # exactly like this.
    bridge = tmp_path / "lemma-bridge"
    bridge.write_text("#!/usr/bin/env python3\n")
    bridge.chmod(0o600)

    monkeypatch.setenv("RUNTIME_URL", "http://127.0.0.1:1")
    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(bridge)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )

    # A mutation must fail rather than quietly succeed.
    with pytest.raises(ProviderRejected):
        await provider.release(
            _instance(uuid4()),
            kind=SandboxKind.WORKSPACE,
            deadline_at=_deadline(),
        )

    # And a status must not claim the sandbox is gone when we could not ask.
    with pytest.raises(SandboxUnavailable):
        await provider.create_directory(
            _instance(uuid4()), path="/workspace/x", deadline_at=_deadline()
        )
