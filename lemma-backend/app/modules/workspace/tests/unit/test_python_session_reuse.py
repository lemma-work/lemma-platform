"""A stateful interpreter is created once per container, not once per call.

The workspace session object is rebuilt for every tool call, so the flag it used
to track this was always False and `execute_python` opened with a
`create_python_session` round trip every single time -- the same shape as the
shell path's redundant ensures, in the one place traces could not see because
this path carried no spans at all.

Keyed by the container epoch rather than the storage generation, because a
kernel is memory: it dies with the container that hosts it, where a workspace
directory lives on the volume and survives exactly that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.workspace import sandbox_session as session_module
from app.modules.workspace.sandbox_session import (
    SandboxWorkspaceSession,
    forget_python_sessions,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.creates = 0

    async def create_python_session(self, *args, **kwargs) -> None:
        self.creates += 1


@pytest.fixture(autouse=True)
def _isolate_registry():
    session_module._python_sessions_observed.clear()
    yield
    session_module._python_sessions_observed.clear()


def _session(client, sandbox_id, *, epoch: int | None, session_id: str = "shell-1"):
    return SandboxWorkspaceSession(
        client=client,
        sandbox_id=str(sandbox_id),
        session_id=session_id,
        allocation_epoch=epoch,
    )


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_a_rebuilt_session_does_not_recreate_the_interpreter() -> None:
    client = _RecordingClient()
    sandbox_id = uuid4()

    for _ in range(5):
        # A fresh object each time, exactly as get_session hands one back.
        await _session(client, sandbox_id, epoch=1)._ensure_python_session(_deadline())

    assert client.creates == 1


@pytest.mark.asyncio
async def test_a_recreated_container_gets_a_new_interpreter() -> None:
    """The kernel died with the old container, so it must be created again."""
    client = _RecordingClient()
    sandbox_id = uuid4()

    await _session(client, sandbox_id, epoch=1)._ensure_python_session(_deadline())
    await _session(client, sandbox_id, epoch=2)._ensure_python_session(_deadline())

    assert client.creates == 2


@pytest.mark.asyncio
async def test_two_conversations_do_not_share_an_interpreter() -> None:
    client = _RecordingClient()
    sandbox_id = uuid4()

    await _session(
        client, sandbox_id, epoch=1, session_id="shell-a"
    )._ensure_python_session(_deadline())
    await _session(
        client, sandbox_id, epoch=1, session_id="shell-b"
    )._ensure_python_session(_deadline())

    assert client.creates == 2


@pytest.mark.asyncio
async def test_forgetting_a_sandbox_forces_a_fresh_interpreter() -> None:
    client = _RecordingClient()
    sandbox_id = uuid4()
    await _session(client, sandbox_id, epoch=1)._ensure_python_session(_deadline())

    forget_python_sessions(sandbox_id)

    await _session(client, sandbox_id, epoch=1)._ensure_python_session(_deadline())
    assert client.creates == 2


@pytest.mark.asyncio
async def test_without_an_epoch_nothing_is_remembered() -> None:
    """A caller that cannot say which container it means gets the old behaviour."""
    client = _RecordingClient()
    sandbox_id = uuid4()

    for _ in range(3):
        await _session(client, sandbox_id, epoch=None)._ensure_python_session(
            _deadline()
        )

    assert client.creates == 3
