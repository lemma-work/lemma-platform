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
from collections.abc import AsyncIterator, Awaitable, Callable
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
from app.modules.test_support.e2e.agent_host_binary import agent_host_binary

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


class _Host:
    """The paired binary's ``serve``, which the test may stop and start."""

    def __init__(self, start: Callable[[], Awaitable[asyncio.subprocess.Process]]):
        self._start = start
        self._server: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self._server = await self._start()

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None or server.returncode is not None:
            return
        server.terminate()
        try:
            async with asyncio.timeout(10):
                await server.wait()
        except TimeoutError:
            server.kill()
            await server.wait()

    async def restart(self) -> None:
        await self.stop()
        await self.start()


@asynccontextmanager
async def _running_host(
    root: Path, base_url: str, pairing_code: SecretStr
) -> AsyncIterator[tuple[Path, _Host]]:
    """Pair and serve the real binary with host execution turned on.

    Built and located exactly as ``test_agent_host_process_e2e`` does: the
    debug build in the repository, or ``LEMMA_AGENT_HOST_E2E_BINARY``. Default
    roots go under ``root/lemma`` rather than the developer's own ``~/lemma``.
    """
    binary = agent_host_binary()
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

        async def serve() -> asyncio.subprocess.Process:
            return await asyncio.create_subprocess_exec(
                str(binary),
                "--data-dir",
                str(data),
                "serve",
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )

        host = _Host(serve)
        await host.start()
        try:
            yield workspaces, host
        finally:
            await host.stop()


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

    async with _running_host(tmp_path, base_url, pairing_code) as (
        workspaces,
        host_process,
    ):
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

        # --- the Mac restarts: it forgot every open workspace ---------------
        # The next op is answered `workspace_not_open`; the provider re-opens
        # on the host the conversation's run chose, and the Mac puts it back
        # in the folder it remembers for the conversation -- here with no
        # conversation row for Lemma to read folder inputs from at all.
        # Nothing about the folder is stored on Lemma's side.
        await host_process.restart()

        async def after_restart() -> dict | None:
            # The row can still read "online" from before the restart; until
            # the new link is up an op is "This Mac is not connected".
            try:
                return await session.exec_command(cmd="pwd && cat f.txt", timeout=60)
            except SandboxError:
                return None

        after = await eventually(
            label="a command on the restarted host",
            probe=after_restart,
            done=lambda ran: ran is not None and ran.get("exit_code") == 0,
            timeout_seconds=60,
        )
        assert after is not None and after["exit_code"] == 0, after
        assert after["stdout"].splitlines() == [str(root.resolve()), "hi"], after

        await service.release(workspace.sandbox_id)


def _lemma_cli_root(parent: Path) -> Path:
    """A CLI root laid out as the host pack lays it out, built the same way.

    `install_lemma_cli` is the host-pack builder's own step, run against this
    interpreter: the launcher it writes finds the Python beside it, so the
    pack's `backend/python` is this one, linked.
    """
    import importlib.util
    import sys
    import tempfile

    spec = importlib.util.spec_from_file_location(
        "build_local_host_pack", _REPOSITORY / "scripts" / "build_local_host_pack.py"
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    backend = parent / "local-runtime" / "backend"
    (backend / "python" / "bin").mkdir(parents=True)
    interpreter = backend / "python" / "bin" / "python3"
    interpreter.symlink_to(Path(sys._base_executable).resolve())
    with tempfile.TemporaryDirectory() as wheels:
        builder.install_lemma_cli(parent / "local-runtime", interpreter, Path(wheels))
    return backend


@pytest.mark.asyncio
async def test_lemma_in_a_host_command_is_this_release_acting_as_the_run(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    tmp_path: Path,
) -> None:
    """`lemma` on the Mac is the CLI Lemma shipped, signed in as the run.

    Two things had to be true and neither was. The Mac had no `lemma` unless
    its owner installed one, of whatever version; and the identity a command
    was handed addressed the backend as `host.lemma.internal`, which only the
    VM's containers resolve. Here the backend names its own CLI in
    `workspace.open`, the real host puts it first on `PATH` under Seatbelt, and
    the command's environment carries the run's delegated session with the
    addresses the Mac reaches -- so `lemma me get` answers as the owner.
    """
    from app.modules.workspace.services.host_environment import with_host_addresses
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    cli_root = _lemma_cli_root(tmp_path / "pack")
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings", json={"display_name": "host lemma cli"}
    )
    assert minted.is_success, minted.text
    base_url = backend_server["host_base_url"]

    async with _running_host(
        tmp_path, base_url, SecretStr(minted.json()["pairing_code"])
    ) as (_workspaces, _host):
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=30
        ) as client:
            listed = await client.get("/me/runtime/agent-hosts")
            assert listed.is_success, listed.text
            (host,) = AgentHostListResponse.model_validate(listed.json()).items
        owner_id = host.user_id
        await eventually(
            label="host_execution reported by the real host",
            probe=partial(host_execution_host_id, owner_id),
            done=lambda found: found == host.id,
            timeout_seconds=60,
        )

        routing = HostRoutingProvider(
            _VmProvider(), build_host_provider(lemma_cli=str(cli_root))
        )
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
        mine = _conversation(owner_id)
        workspace = await choose_host_workspace(
            conversation=mine, agent_run=_run(mine), user_id=owner_id, facts=facts
        )
        assert workspace is not None

        sandboxes = WorkspaceSandboxService()
        try:
            identity = await sandboxes.get_env_vars(
                owner_id,
                None,
                session_id=f"shell-{mine.id.hex}",
                conversation_id=mine.id,
            )
        finally:
            await sandboxes.close()
        session = HostWorkspaceSession(
            root=workspace.root,
            client=LocalSandboxClient(service),
            sandbox_id=workspace.sandbox_id,
            owns_client=False,
            # The Mac reaches this test's backend where the CLI is told to
            # look for it; the configured address is some other server's.
            env_vars=with_host_addresses(identity) | {"LEMMA_BASE_URL": base_url},
        )

        ran = await session.exec_command(
            cmd=(
                'command -v lemma && echo "conversation=$LEMMA_CONVERSATION_ID" '
                "&& lemma me get --output json"
            ),
            timeout=120,
        )
        assert ran["exit_code"] == 0, ran
        found, conversation, *answer = ran["stdout"].splitlines()
        assert Path(found).resolve() == (cli_root / "bin" / "lemma").resolve(), ran
        assert conversation == f"conversation={mine.id}", ran
        assert str(owner_id) in "\n".join(answer), ran

        await service.release(workspace.sandbox_id)
