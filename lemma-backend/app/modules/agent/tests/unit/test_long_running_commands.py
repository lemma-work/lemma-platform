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
    # or trimmed prompt still leaves the model holding a process_id. It names
    # `wait_for` now — the answer to a slow command is to wait for it, not to
    # ask about it repeatedly.
    assert "wait_for" in description
    assert "never re-run" in description.lower()


def test_completed_is_not_described_as_a_tty_quirk() -> None:
    """A non-TTY build that outlives its wait window returns false too."""
    description = _describe(ExecCommandResult, "completed")

    assert "outlived" in description
    assert "still running" in description
    assert "not cancelled" in description


def test_process_id_explains_how_to_wait_for_it() -> None:
    """It must say what to do with the handle — and that is now to wait.

    The pin has moved twice, and both times for the same reason: an agent left
    holding a `process_id` with no instruction invents one. It used to be
    `chars=''`, which reaches the same code as omitting the argument while
    inviting an empty-string value; then a `manage_process` loop, which cost a
    model round trip per check. The instruction is `wait_for`, and it belongs in
    the schema rather than only the prompt, which may be deferred or trimmed.
    """
    description = _describe(ExecCommandResult, "process_id")

    assert "wait_for(process_id=...)" in description
    assert "chars=''" not in description and 'chars=""' not in description


def test_exec_command_docstring_teaches_waiting_not_looping() -> None:
    """This docstring is the model's primary instruction for long commands."""
    from app.modules.agent.tools.workspace_cli.pydantic_adapter import exec_command

    doc = exec_command.__doc__ or ""
    assert "completed: false" in doc
    assert "wait_for" in doc
    assert "Never re-run" in doc
    assert 'action="list"' in doc
    # `manage_process` is still named, for input and for recovery — it just is
    # not the answer to "it has not finished yet".
    assert "manage_process" in doc


def test_workspace_prompt_covers_long_commands() -> None:
    from app.modules.agent.domain.prompts import load_workspace_cli_prompt

    prompt = load_workspace_cli_prompt()
    assert "Long-running commands" in prompt
    assert "exit_code" in prompt
    assert "wait_for(" in prompt


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
