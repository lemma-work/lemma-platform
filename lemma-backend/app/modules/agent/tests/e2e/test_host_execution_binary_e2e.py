"""Host execution through the real ``lemma-agent-host`` and its exec-server.

docs/architecture/desktop-host-execution.md §8, "Backend e2e". Nothing on the
host side is a stand-in: the binary pairs over HTTP, connects its link to a
real backend server, reports ``host_execution`` from its config, and runs every
op in an exec-server under ``sandbox-exec``. On the backend side the real
provider, routing provider, sandbox service, link session and Redis hop carry
each operation. What the test states rather than derives is only what a
server-mode e2e cannot be: that this is a Desktop install, and who owns it.

macOS only: Seatbelt is the boundary under test, and it exists nowhere else.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from sandbox_runtime.errors import SandboxError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.api.agent_host_schemas import AgentHostListResponse
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.infrastructure.agent_host.host_execution import (
    host_execution_host_id,
)
from app.modules.agent.services.host_execution_selection import (
    HostExecutionFacts,
    choose_host_workspace,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.domain.host_execution import HOST_EXECUTION_PROVIDER
from app.modules.workspace.host_workspace_session import HostWorkspaceSession
from app.modules.workspace.providers.base import ProviderStorageKind
from app.modules.workspace.providers.host_routing import HostRoutingProvider
from app.modules.workspace.services.host_workspace import (
    build_host_provider,
    open_host_workspace,
)
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.sandbox_service import SandboxService

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.local_cli,
    pytest.mark.skipif(
        platform.system() != "Darwin" or shutil.which("sandbox-exec") is None,
        reason="host execution runs under Seatbelt, which only macOS has",
    ),
]

_REPOSITORY = Path(__file__).resolve().parents[6]


class _VmProvider:
    """The Desktop VM's place in the router, which no call here may reach."""

    name = "lemma_local"
    storage_kind = ProviderStorageKind.VOLUME
    resumes_stopped_instances = False
    capabilities = frozenset()
    provider_name = "Desktop"

    def __getattr__(self, attribute: str):
        raise AssertionError(f"a host sandbox reached the VM provider: {attribute}")


@asynccontextmanager
async def _running_host(
    root: Path, base_url: str, pairing_code: SecretStr
) -> AsyncIterator[Path]:
    """Pair and serve the real binary with host execution turned on.

    Built and located exactly as ``test_agent_host_process_e2e`` does: the
    debug build in the repository, or ``LEMMA_AGENT_HOST_E2E_BINARY``. Default
    roots go under ``root/lemma`` rather than the developer's own ``~/lemma``.
    """
    binary = Path(
        os.environ.get(
            "LEMMA_AGENT_HOST_E2E_BINARY",
            str(_REPOSITORY / "desktop/target/debug/lemma-agent-host"),
        )
    )
    assert binary.is_file(), "Build lemma-agent-host first: make desktop-agent-host-e2e"
    data = root / "host-data"
    shims = root / "shim-bin"
    workspaces = root / "lemma"
    shims.mkdir()
    workspaces.mkdir()
    environment = {
        **os.environ,
        "LEMMA_AGENT_HOST_PATH": str(shims),
        "LEMMA_AGENT_HOST_SKIP_ADAPTER_DOWNLOAD": "1",
        "LEMMA_AGENT_HOST_WORKSPACE_ROOT": str(workspaces),
        "RUST_LOG": "lemma_agent_host=info",
    }
    with (root / "host.log").open("wb") as log:

        async def run(*arguments: str) -> None:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "--data-dir",
                str(data),
                *arguments,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
            try:
                async with asyncio.timeout(30):
                    assert await process.wait() == 0, f"`{arguments[0]}` failed"
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

        await run(
            "connect",
            "--url",
            base_url,
            "--pairing-code",
            pairing_code.get_secret_value(),
            "--allow-insecure-http",
        )
        # Writes `host_execution: true` into the host's config.json.
        await run("host-execution", "enable")
        server = await asyncio.create_subprocess_exec(
            str(binary),
            "--data-dir",
            str(data),
            "serve",
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:
            yield workspaces
        finally:
            if server.returncode is None:
                server.terminate()
                try:
                    async with asyncio.timeout(10):
                        await server.wait()
                except TimeoutError:
                    server.kill()
                    await server.wait()


def _conversation(user_id: UUID) -> Conversation:
    return Conversation(
        user_id=user_id,
        pod_id=uuid4(),
        metadata={"cwd": "/home/user/lemma/c/2026-09-25/hostexec"},
        created_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
    )


def _run(conversation: Conversation) -> AgentRun:
    return AgentRun(
        conversation_id=conversation.id,
        started_at=datetime.now(timezone.utc),
        metadata={"source": "user_message"},
    )


@pytest.mark.asyncio
async def test_the_real_binary_runs_the_paired_users_command_on_the_host(
    scenario: E2EScenario, backend_server: dict[str, str], tmp_path: Path
) -> None:
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings", json={"display_name": "host execution"}
    )
    assert minted.is_success, minted.text
    pairing_code = SecretStr(minted.json()["pairing_code"])
    base_url = backend_server["host_base_url"]

    async with _running_host(tmp_path, base_url, pairing_code) as workspaces:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=30
        ) as client:
            listed = await client.get("/me/runtime/agent-hosts")
            assert listed.is_success, listed.text
            (host,) = AgentHostListResponse.model_validate(listed.json()).items
        owner_id = host.user_id

        # The capability arrives the way production sees it: from the host's
        # own hello or control frame, into the host row selection reads.
        await eventually(
            label="host_execution reported by the real host",
            probe=partial(host_execution_host_id, owner_id),
            done=lambda found: found == host.id,
            timeout_seconds=60,
        )

        routing = HostRoutingProvider(_VmProvider(), build_host_provider())
        service = SandboxService(
            provider=routing,
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        )
        recorded: dict[UUID, dict] = {}

        async def record(run_id: UUID, value: dict) -> None:
            recorded[run_id] = value

        async def recall(run_id: UUID) -> dict | None:
            return recorded.get(run_id)

        facts = HostExecutionFacts(
            is_desktop=lambda: True,
            usable_host=host_execution_host_id,
            open_workspace=partial(open_host_workspace, service=service),
            recorded=recall,
            record=record,
        )

        # --- a user with no paired host lands in the VM, and routes there ----
        teammate = uuid4()
        teammates = _conversation(teammate)
        assert (
            await choose_host_workspace(
                conversation=teammates,
                agent_run=_run(teammates),
                user_id=teammate,
                facts=facts,
            )
            is None
        )
        assert routing.for_sandbox(teammate) is routing.default

        # --- the paired user's run opens a workspace on the Mac --------------
        mine = _conversation(owner_id)
        workspace = await choose_host_workspace(
            conversation=mine, agent_run=_run(mine), user_id=owner_id, facts=facts
        )
        assert workspace is not None
        root = Path(workspace.root)
        assert root.is_dir()
        assert root.resolve().is_relative_to(workspaces.resolve())
        assert root.name == "hostexec"
        handle = await service.ensure(workspace.sandbox_id)
        assert handle.provider == HOST_EXECUTION_PROVIDER

        session = HostWorkspaceSession(
            root=workspace.root,
            client=LocalSandboxClient(service),
            sandbox_id=workspace.sandbox_id,
            owns_client=False,
        )

        # A command, on the Mac, in the root.
        ran = await session.exec_command(cmd="echo hi > f.txt && uname", timeout=60)
        assert ran["exit_code"] == 0, ran
        assert "Darwin" in ran["stdout"], ran
        assert (root / "f.txt").read_text() == "hi\n"

        stat = await session.stat_file("f.txt")
        assert stat.size_bytes == 3
        assert stat.sha256 == "sha256:" + hashlib.sha256(b"hi\n").hexdigest()
        assert await session.read_file("f.txt") == b"hi\n"

        # A write larger than one frame: chunked, digest-checked, whole.
        body = os.urandom(2 * 1024 * 1024 + 4321)
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        written = await session.write_file(
            "big.bin", body, expected_sha256=digest, timeout=120
        )
        assert written.size_bytes == len(body)
        assert (root / "big.bin").read_bytes() == body
        assert await session.read_file("big.bin", timeout=120) == body

        # Seatbelt, through the whole chain: the home folder outside the root
        # is not writable, by a command or by a file op.
        home = Path(os.environ["HOME"])
        escapee = home / f"lemma-host-execution-e2e-{uuid4().hex}"
        try:
            denied = await session.exec_command(cmd=f"touch '{escapee}'", timeout=60)
            assert denied["exit_code"] not in (0, None), denied
            assert "not permitted" in (denied["stderr"] + denied["stdout"]), denied
            with pytest.raises(SandboxError):
                await session.write_file(str(escapee), b"nope")
            assert not escapee.exists()
        finally:
            escapee.unlink(missing_ok=True)

        await service.release(workspace.sandbox_id)
