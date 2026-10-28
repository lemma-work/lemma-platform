"""One fake standing in for every repository a conversation use case reads.

The use cases build their repositories from the unit of work
(``infrastructure/db``), one per aggregate. A unit test that drives the real
use case with a hand-written fake points each of them at that fake, here, rather
than threading one object through every constructor.
"""

from __future__ import annotations

#: The attribute each repository is held under, on every use case that holds one.
REPOSITORY_ATTRIBUTES = (
    "conversations",
    "runs",
    "messages",
    "queued_messages",
    "pauses",
)

#: The use cases a use case is built with, which hold repositories of their own.
_COORDINATORS = (
    "admit",
    "reads",
    "start_conversation",
    "supersede",
    "resume",
    "reconcile",
    "resolve",
)


def wire_repositories[T](target: T, fake: object) -> T:
    """Point every repository ``target`` and the use cases it holds at ``fake``."""
    seen: set[int] = set()

    def visit(holder: object) -> None:
        if id(holder) in seen or not hasattr(holder, "__dict__"):
            return
        seen.add(id(holder))
        held = vars(holder)
        for name in REPOSITORY_ATTRIBUTES:
            if name in held:
                setattr(holder, name, fake)
        for name in _COORDINATORS:
            if held.get(name) is not None:
                visit(held[name])

    visit(target)
    return target


def pause_for_call(call, *, kind=None, **fields):
    """The pause a stored call opened, as the writer would have opened it."""
    from uuid import uuid4

    from app.modules.agent.agent_host.domain.permissions import (
        AGENT_HOST_PERMISSION_KEY,
    )
    from app.modules.agent.domain.pause_opening import AgentHostPermissionRequest
    from app.modules.agent.domain.pauses import Pause
    from app.modules.agent.domain.state_kinds import PauseKind

    args = call.tool_args if isinstance(call.tool_args, dict) else {}
    marker = args.get(AGENT_HOST_PERMISSION_KEY)
    if kind is None:
        kind = {
            "ask_user": PauseKind.QUESTION,
            "browser_sign_in": PauseKind.SIGN_IN,
            "wait_for": PauseKind.WAIT,
        }.get(call.tool_name)
    if kind is None:
        kind = (
            PauseKind.HOST_PERMISSION
            if isinstance(marker, dict)
            else PauseKind.APPROVAL
        )
    host_permission = (
        AgentHostPermissionRequest.model_validate(
            {**marker, "tool_name": args.get("tool_name")}
        )
        if kind is PauseKind.HOST_PERMISSION
        else None
    )
    return Pause(
        conversation_id=call.conversation_id,
        run_id=call.agent_run_id or uuid4(),
        pod_id=uuid4(),
        tool_call_id=call.tool_call_id,
        tool_name=call.tool_name,
        kind=kind,
        host_permission=host_permission,
        call=call,
        **fields,
    )
