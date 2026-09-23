"""The runtime-readiness probe's polling contract.

`ensure_runtime_serving` exists because a VM outlives the process inside it,
so readiness is an HTTP answer, not `is_running()`. But the answer the probe
gets first is not always the answer that is true: E2B's edge answers 502 both
for a runtime that has died and for a sandbox it has not routed to yet, and
the seconds after a create or a resume are the second kind. Taking the first
502 as final failed the whole provision during ordinary edge lag -- and with
it the first tool call of every run that landed on a cold sandbox.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.workspace.providers.base import ProviderFailed
from app.modules.workspace.providers.e2b_common import (
    _poll_runtime,
    ensure_runtime_serving,
)


class _ScriptedHttpx:
    """An `httpx.AsyncClient` stand-in answering from a script.

    Entries are status codes, or ``None`` for a transport failure. Exhausting
    the script repeats its last entry, so "never stops failing" does not need
    an infinite list.
    """

    def __init__(self, script: list):
        self._script = script
        self.calls = 0

    async def __aenter__(self) -> "_ScriptedHttpx":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str):
        entry = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if entry is None:
            raise httpx.ConnectError("edge not listening yet")
        return httpx.Response(status_code=entry)


@pytest.fixture
def scripted_httpx(monkeypatch: pytest.MonkeyPatch):
    """Install the scripted client and hand the test its instance."""

    instances: list[_ScriptedHttpx] = []

    def factory(script: list) -> _ScriptedHttpx:
        instance = _ScriptedHttpx(script)
        instances.append(instance)
        return instance

    def construct(*_args, **_kwargs) -> _ScriptedHttpx:
        assert instances, "the test must script its answers before polling"
        return instances[-1]

    monkeypatch.setattr(httpx, "AsyncClient", construct)
    return factory


class _Sandbox:
    """Just enough of the SDK sandbox for the probe."""

    def __init__(self, *, running: bool = True) -> None:
        self._running = running

    async def is_running(self) -> bool:
        return self._running

    def get_host(self, port: int) -> str:
        return f"{port}-i12345.e2b.dev"


async def test_a_transient_502_is_polled_through(scripted_httpx) -> None:
    """Edge lag after a create or resume answers 502 first, 404 once routed.

    This is the 2026-08-20 first-tool-call failure: the probe took the first
    502 as final, so a healthy sandbox still being routed to was declared
    dead and the provision was rejected.
    """
    client = scripted_httpx([502, 502, 404])

    status = await _poll_runtime("https://8080-i12345.e2b.dev/health", 5.0)

    assert status == 404
    assert client.calls == 3


async def test_a_persistent_502_still_fails_with_the_status(scripted_httpx) -> None:
    """The P0 the probe exists for: a runtime that never comes back.

    Polling must not turn that into silence -- the failure keeps the status,
    because "answered 502" and "answered nothing" are different diagnoses.
    """
    scripted_httpx([502])

    status = await _poll_runtime("https://8080-i12345.e2b.dev/health", 0.3)

    assert status == 502


async def test_a_port_that_never_answers_returns_none(scripted_httpx) -> None:
    scripted_httpx([None])

    status = await _poll_runtime("https://8080-i12345.e2b.dev/health", 0.3)

    assert status is None


async def test_a_healthy_runtime_is_answered_immediately(scripted_httpx) -> None:
    """The budget is only spent on sandboxes that are not serving; a healthy
    one must not pay for it."""
    client = scripted_httpx([404])

    status = await _poll_runtime("https://8080-i12345.e2b.dev/health", 5.0)

    assert status == 404
    assert client.calls == 1


async def test_ensure_waits_out_edge_lag_after_a_resume(scripted_httpx) -> None:
    scripted_httpx([502, 502, 404])

    await ensure_runtime_serving(_Sandbox(), "i12345", runtime_port=8080)


async def test_ensure_reports_the_status_of_a_dead_runtime(scripted_httpx) -> None:
    scripted_httpx([502])

    with pytest.raises(ProviderFailed) as failure:
        await ensure_runtime_serving(
            _Sandbox(), "i12345", runtime_port=8080, budget_seconds=0.3
        )

    assert "502" in str(failure.value)


async def test_a_stopped_vm_is_not_polled_at_all(scripted_httpx) -> None:
    """A VM that is not running fails before any HTTP is attempted.

    Raised inside `sdk_errors`, the failure is classified like any SDK call's:
    an unrecognised shape reads as transient, so this surfaces as
    `SandboxUnavailable` -- retryable, which is the right reading of a VM that
    may simply still be starting.
    """
    from sandbox_runtime.errors import SandboxUnavailable

    with pytest.raises(SandboxUnavailable):
        await ensure_runtime_serving(
            _Sandbox(running=False),
            "i12345",
            runtime_port=8080,
            budget_seconds=0.3,
        )


# ---------------------------------------------------------------------------
# Workspace readiness: one command through the sandbox agent
# ---------------------------------------------------------------------------


class _AgentSandbox(_Sandbox):
    """A sandbox whose agent answers from a script of booleans.

    Each entry is one smoke-command attempt: True answers, False does not.
    Exhausting the script repeats its last entry.
    """

    def __init__(self, script: list[bool], *, running: bool = True) -> None:
        super().__init__(running=running)
        self._script = script
        self.attempts = 0

    async def smoke_command(self) -> bool:
        entry = self._script[min(self.attempts, len(self._script) - 1)]
        self.attempts += 1
        return entry


async def test_a_workspace_is_ready_once_its_agent_answers() -> None:
    """The agent can lag the VM after a create or resume; readiness waits."""
    from app.modules.workspace.providers.e2b_common import ensure_agent_serving

    sandbox = _AgentSandbox([False, False, True])

    await ensure_agent_serving(sandbox, "i12345", budget_seconds=5.0)

    assert sandbox.attempts == 3


async def test_a_workspace_whose_agent_stays_down_is_not_ready() -> None:
    from app.modules.workspace.providers.e2b_common import ensure_agent_serving

    sandbox = _AgentSandbox([False])

    with pytest.raises(ProviderFailed) as failure:
        await ensure_agent_serving(sandbox, "i12345", budget_seconds=0.5)

    assert "never answered a command" in str(failure.value)


async def test_a_stopped_workspace_vm_is_not_polled() -> None:
    from sandbox_runtime.errors import SandboxUnavailable

    from app.modules.workspace.providers.e2b_common import ensure_agent_serving

    with pytest.raises(SandboxUnavailable):
        await ensure_agent_serving(
            _AgentSandbox([True], running=False), "i12345", budget_seconds=0.5
        )


class TestClosingTheBrowserBeforeAPause:
    """The one step that keeps a person's logins, and it had no test at all.

    An E2B workspace pause is `keep_memory=False` -- power loss, as far as
    Chrome is concerned -- and `agent-browser` runs Chrome on a throwaway
    `--user-data-dir` under `/tmp`, copying it to the durable profile only
    on a clean close. So this call is the whole of what makes a sign-in
    outlive a release on the fabric that serves users.

    It was reachable from two source files and named in none. Docker gets
    the same property from the quiescer and is covered end to end; the e2e
    suite pins `WORKSPACE_PROVIDER=docker`, so the covered fabric was the
    one nobody runs on and the uncovered one was dev and prod.
    """

    class _Commands:
        def __init__(self, raises: Exception | None = None) -> None:
            self.calls: list[tuple[str, object]] = []
            self._raises = raises

        async def run(self, command: str, *, timeout=None, **api):
            self.calls.append((command, timeout))
            if self._raises is not None:
                raise self._raises

    class _Sandbox:
        def __init__(self, commands) -> None:
            self.commands = commands

    async def test_it_asks_the_daemon_to_close_every_session(self) -> None:
        """`close --all`, not a signal: the daemon owns the process, and
        `--all` because a sign-in runs in its own `login-<host>` session
        whose profile is just as much the person's."""
        from app.modules.workspace.providers.e2b_common import (
            BROWSER_CLOSE_SECONDS,
            close_browser,
        )

        commands = self._Commands()

        await close_browser(self._Sandbox(commands), "sbx-1")

        assert commands.calls == [("agent-browser close --all", BROWSER_CLOSE_SECONDS)]

    @pytest.mark.parametrize("failure", ["gone", "rejected", "unavailable"])
    async def test_a_browser_that_cannot_be_reached_does_not_fail_the_release(
        self, failure: str
    ) -> None:
        """Best effort, for the same reason Docker's quiesce is: a sandbox
        whose browser cannot be reached is exactly the one most in need of
        being released. Worst case the pause proceeds as it did before this
        existed -- and the person loses a login, which is why the handler
        warns rather than swallowing.
        """
        from sandbox_runtime.errors import SandboxUnavailable

        from app.modules.workspace.providers.base import (
            ProviderGone,
            ProviderRejected,
        )
        from app.modules.workspace.providers.e2b_common import close_browser

        raised = {
            "gone": ProviderGone("gone"),
            "rejected": ProviderRejected("rejected"),
            "unavailable": SandboxUnavailable("unavailable"),
        }[failure]
        commands = self._Commands(raises=raised)

        await close_browser(self._Sandbox(commands), "sbx-1")

        assert commands.calls, "it must still have tried"

    async def test_a_bug_in_this_call_is_reported_as_a_close_that_failed(
        self,
    ) -> None:
        """Pinned because the code used to claim the opposite.

        `sdk_errors` catches every `Exception` and maps it through
        `classify`, and `classify` answers `SandboxUnavailable` for a plain
        `AttributeError` -- so a typo here is caught by the same handler as
        an unreachable sandbox and the release carries on. That is the
        accepted trade, not an accident: the warning names the step, and a
        release logging `browser_close_failed` every single time is a bug
        announcing itself. It is written down here so the next person does
        not read the handler as narrower than it is.
        """
        from app.modules.workspace.providers.e2b_common import close_browser

        commands = self._Commands(raises=AttributeError("renamed in the SDK"))

        await close_browser(self._Sandbox(commands), "sbx-1")

        assert commands.calls, "it must still have tried"
