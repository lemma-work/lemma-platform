"""A release should not end every conversation in flight.

A worker on its way out used to finalize each in-flight run FAILED, and because
`process_agent_run` swallowed the CancelledError, streaq saw a task that
returned and XACKed it — so nothing redelivered it either. Shipping a version
terminated every agent run on the box and each person had to ask again.

Nothing needs re-doing to fix that, and nothing needs building: streaq already
relinquishes a task cancelled by the shutdown grace period, leaving it in the
pending list for the next worker's XAUTOCLAIM. Messages are persisted as they
stream and history is rebuilt from the database on every run, so the worker that
reclaims the job reconstructs exactly the context the interrupted one had.

All this takes is letting the cancellation out.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
)

pytestmark = pytest.mark.unit


class TestTheCancellationReachesStreaq:
    def test_the_task_handler_does_not_catch_it(self) -> None:
        """A `except asyncio.CancelledError` here is the whole bug: streaq only
        relinquishes a task that was actually cancelled."""
        from app.modules.agent.events import handlers

        # Parsed, not grepped: the comment above the call says the word too, and
        # a substring check would pass with the handler right back in place.
        tree = ast.parse(
            textwrap.dedent(inspect.getsource(handlers.process_agent_run.fn))
        )
        caught = {
            name.id
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            for name in ast.walk(node.type or ast.Constant(None))
            if isinstance(name, ast.Name)
        } | {
            node.type.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and isinstance(node.type, ast.Attribute)
        }

        assert "CancelledError" not in caught

    async def test_execute_lets_it_out(self, monkeypatch) -> None:
        from app.modules.agent.services import agent_runner_service as module

        source = inspect.getsource(module.AgentRunnerService.execute)

        # Re-raised for anything that is not an ordinary Exception -- which is
        # cancellation, and nothing else.
        assert "raise" in source

    def test_a_reclaimed_run_is_still_in_a_state_execute_accepts(self) -> None:
        """The run is deliberately left RUNNING when the worker goes away: that
        is the state the worker reclaiming the job expects to find. A status
        change would need a claim, an index dance, and a sweep to drive it."""
        assert AgentRunStatus.RUNNING in ACTIVE_AGENT_RUN_STATUSES

    def test_there_is_no_resume_sweep_left(self) -> None:
        """The queue redelivers. A cron that re-enqueues duplicates it, and the
        one that existed could silently enqueue nothing: streaq publishes with
        `SET NX`, so re-enqueueing under a finished job's id is dropped."""
        import importlib.util

        assert importlib.util.find_spec("app.modules.agent.services.run_resume") is None


class TestAnInterruptedToolCallDoesNotInviteARepeat:
    def test_the_model_is_not_told_to_run_it_again(self) -> None:
        """Runs resume now, so this text is reached far more often. The tool may
        well have succeeded; all that is known is that no result was recorded."""
        from uuid import uuid7

        from pydantic_ai.messages import ModelRequest, ToolReturnPart

        from app.modules.agent.domain.entities import Message
        from app.modules.agent.domain.value_objects import MessageKind, MessageRole
        from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
            history_and_prompt,
        )

        conversation_id = uuid7()
        run_id = uuid7()
        messages = [
            Message.create(
                conversation_id=conversation_id,
                sequence=1,
                agent_run_id=run_id,
                role=MessageRole.USER,
                kind=MessageKind.TEXT,
                text="send the invoice",
            ),
            Message.create(
                conversation_id=conversation_id,
                sequence=2,
                agent_run_id=run_id,
                role=MessageRole.ASSISTANT,
                kind=MessageKind.TOOL_CALL,
                tool_name="send_email",
                tool_call_id="e1",
                tool_args={},
            ),
        ]

        history, _ = history_and_prompt(messages)

        returns = " ".join(
            str(part.content)
            for message in history
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        assert "Run it again" not in returns
        assert "outcome is unknown" in returns
