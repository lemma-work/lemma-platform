"""The contract a long build sees.

The mechanism was always correct — a command that outlives its call keeps
running and can be polled to completion — but the tool schema told the model the
opposite, so agents re-ran builds they thought had been cancelled. These tests
pin the honest contract, because it only reaches the model through those strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecCommandResult,
)

pytestmark = pytest.mark.unit


def _describe(model, field: str) -> str:
    return model.model_fields[field].description or ""


def test_timeout_seconds_no_longer_promises_completion() -> None:
    """It used to claim it "always returns `completed: true` with no
    `process_id`", which is false for any command slower than the timeout."""
    description = _describe(ExecCommandRequest, "timeout_seconds")

    assert "always returns" not in description
    assert "completed: false" in description
    assert "keeps running" in description
    # The recovery path has to be in the schema, not just the prompt: a deferred
    # or trimmed prompt still leaves the model holding a process_id.
    assert "manage_process" in description
    assert "never re-run" in description.lower()


def test_completed_is_not_described_as_a_tty_quirk() -> None:
    """A non-TTY build that outlives its wait window returns false too."""
    description = _describe(ExecCommandResult, "completed")

    assert "outlived" in description
    assert "still running" in description
    assert "not cancelled" in description


def test_process_id_explains_how_to_poll() -> None:
    """It must still say how to poll -- just not with an empty string.

    This used to assert the description contained `chars=''`, pinning an idiom
    that reaches exactly the same code as omitting the argument while inviting
    the model to emit an empty-string value. The pin moves to the replacement
    rather than being dropped: an agent still has to learn how to poll.
    """
    description = _describe(ExecCommandResult, "process_id")

    assert "manage_process" in description
    assert "process_id=..." in description
    assert "chars=''" not in description and 'chars=""' not in description


def test_exec_command_docstring_teaches_the_poll_loop() -> None:
    """This docstring is the model's primary instruction for long commands."""
    from app.modules.agent.tools.workspace_cli.pydantic_adapter import exec_command

    doc = exec_command.__doc__ or ""
    assert "completed: false" in doc
    assert "manage_process" in doc
    assert "Never re-run" in doc
    assert 'action="list"' in doc


def test_workspace_prompt_covers_long_commands() -> None:
    from app.modules.agent.domain.prompts import load_workspace_cli_prompt

    prompt = load_workspace_cli_prompt()
    assert "Long-running commands" in prompt
    assert "exit_code" in prompt
    assert "manage_process" in prompt


@pytest.mark.asyncio
async def test_the_reaper_only_terminates_processes_past_their_deadline() -> None:
    """A build must be allowed to outlive the call that started it; a forgotten
    `npm run dev` must not outlive the sandbox."""
    from sandbox_runtime.workspace.process_manager import ProcessManager

    class _Fake:
        def __init__(self, name, deadline_at, running=True):
            self.operation_id = name
            self.deadline_at = deadline_at
            self.needs_quiesce = running
            self.terminated = False

        async def terminate(self, grace):
            del grace
            self.terminated = True

    now = datetime.now(timezone.utc)
    still_building = _Fake("building", now + timedelta(minutes=30))
    abandoned = _Fake("abandoned", now - timedelta(minutes=1))
    already_done = _Fake("done", now - timedelta(minutes=1), running=False)
    no_deadline = _Fake("no-deadline", None)

    manager = ProcessManager()
    manager._processes = {  # noqa: SLF001 - exercising the sweep directly
        item.operation_id: item
        for item in (still_building, abandoned, already_done, no_deadline)
    }

    reaped = await manager.reap_expired()

    assert reaped == ("abandoned",)
    assert abandoned.terminated
    assert not still_building.terminated
    assert not already_done.terminated
    assert not no_deadline.terminated
