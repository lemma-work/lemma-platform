"""ACP permission requests expressed as ordinary Lemma approvals.

An Agent Host run pauses when its ACP agent asks to use a native tool. The host
holds that request open (``desktop/agent-host/src/permissions/``) until a decision
comes back as a ``RESOLVE_PERMISSION`` command, so the pause sits *inside* a
live run rather than at a run boundary — unlike ``ask_user`` /
``request_approval``, which end their run and resume through a new one.

Rendering the request as a ``request_approval`` tool call is what lets one
approval path serve every entry point: the web client reads the persisted call,
Slack/Teams/Telegram render native buttons from it, and all of them resolve
through the same endpoint. Only the *destination* of the decision differs — a
host command instead of a resumed pydantic-ai run — and that is decided by the
pause's kind, ``HOST_PERMISSION``, which carries the request it answers.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.pause_opening import (
    AgentHostPermissionOption,
    AgentHostPermissionRequest,
    PauseOpening,
)
from app.modules.agent.domain.pauses import CALL_NAMES
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunApprovalDecision,
    JsonObject,
    MessageDraft,
)

#: Under a permission request's call arguments: what the agent would run and
#: the options it offered, for the web card to show. Display only -- the
#: pause's ``host_permission`` is what the decision is routed by.
AGENT_HOST_PERMISSION_KEY = "agent_host_permission"


def _normalized_kind(value: object) -> str:
    """Fold ``allow_once`` / ``allowOnce`` / ``ALLOW_ONCE`` to one spelling."""
    return "".join(
        character for character in str(value or "").lower() if character.isalnum()
    )


_PERMISSION_APPROVAL_PREFIX = "agent-host-permission:"

#: On a permission payload from the host: the request was answered by an
#: approval the person gave on an earlier turn, named by its ``approval_id``.
PRE_APPROVED_KEY = "pre_approved"


def permission_approval_tool_call_id(request_id: str) -> str:
    """Namespace the approval's call id away from the native tool call's own.

    The ACP request id *is* the native tool call id, which the host already
    reported as its own tool call. Reusing it here would make one
    ``tool_call_id`` address two different calls, so the approval gets a
    prefixed id and carries the raw request id in its arguments.
    """
    return f"{_PERMISSION_APPROVAL_PREFIX}{request_id}"


def is_permission_approval_tool_call_id(tool_call_id: str) -> bool:
    """Whether this call id is an Agent Host permission request's approval."""
    return tool_call_id.startswith(_PERMISSION_APPROVAL_PREFIX)


def permission_approval_tool_args(
    payload: JsonObject,
    *,
    request_id: str,
    tool_name: str | None = None,
) -> JsonObject:
    """Build ``request_approval`` arguments from an ACP permission payload.

    Deliberately shaped like the arguments the ``request_approval`` tool writes
    (``title`` / ``reason`` / ``tool_name``) so every renderer — web cards and
    the surface approval plan alike — needs no Agent Host special case. There
    is no ``args`` key: nothing here is executed by Lemma.

    ``payload`` is the host's normalized request
    (docs/architecture/agent-host-events.md#permission-requests): ``tool`` is
    the same ``AgentHostToolRef`` the gated ``tool_call`` carried, so its
    ``name`` is already Lemma's word for the tool and its ``title`` the
    adapter's own description of this call. ``tool_name`` is the name the
    caller already announced the gated call under, preferred so the card and
    the call it interrupts cannot disagree.
    """
    tool = payload.get("tool")
    tool = tool if isinstance(tool, dict) else {}
    title = (
        _first_string(payload, "title")
        or _first_string(tool, "title")
        or "The local agent needs permission"
    )
    # What the agent would run, for the card to show under the marker rather
    # than as ``args``: shown to the person deciding, never executed by Lemma.
    request = permission_request(payload, request_id=request_id, tool_name=tool_name)
    return {
        "title": title,
        "reason": _first_string(payload, "message", "reason"),
        "tool_name": request.tool_name,
        AGENT_HOST_PERMISSION_KEY: request.model_dump(
            mode="json", include={"request_id", "input", "options"}
        ),
    }


def permission_request(
    payload: JsonObject, *, request_id: str, tool_name: str | None = None
) -> AgentHostPermissionRequest:
    """The request the host holds open, as its pause carries it."""
    tool = payload.get("tool")
    tool = tool if isinstance(tool, dict) else {}
    tool_input = payload.get("input")
    return AgentHostPermissionRequest(
        request_id=request_id,
        options=_parsed_options(payload.get("options")),
        input=tool_input if isinstance(tool_input, dict) else {},
        tool_name=tool_name or _first_string(tool, "name") or "native tool",
    )


def permission_approval_events(
    *,
    agent_run_id: UUID,
    request_id: str,
    sequence: int,
    payload: JsonObject,
    metadata: JsonObject,
    tool_name: str | None = None,
) -> list[AgentEvent]:
    """The events one parked ACP permission request becomes.

    A persisted ``request_approval`` call — what the web client renders and what
    the approvals endpoint resolves — plus a STATUS event carrying the same
    identity, which is how a surface observer learns to render native buttons.
    Deliberately no WAITING event: WAITING ends a run, and this one continues
    the moment the host has its answer.
    """
    tool_call_id = permission_approval_tool_call_id(request_id)
    return [
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name=CALL_NAMES[PauseKind.HOST_PERMISSION],
                tool_call_id=tool_call_id,
                tool_args=permission_approval_tool_args(
                    payload,
                    request_id=request_id,
                    tool_name=tool_name,
                ),
                metadata=metadata,
                pause=PauseOpening(
                    kind=PauseKind.HOST_PERMISSION,
                    host_permission=permission_request(
                        payload, request_id=request_id, tool_name=tool_name
                    ),
                ),
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
        AgentEvent(
            type=AgentEventType.STATUS,
            data={
                "status": "permission_request",
                "kind": CALL_NAMES[PauseKind.HOST_PERMISSION],
                "tool_call_id": tool_call_id,
                "detail": payload,
                **metadata,
            },
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
    ]


def pre_approved_by(payload: JsonObject) -> str | None:
    """The earlier approval the host answered this request with, if it did."""
    marker = payload.get(PRE_APPROVED_KEY)
    approval_id = marker.get("approval_id") if isinstance(marker, dict) else None
    return approval_id if isinstance(approval_id, str) and approval_id else None


def pre_approved_events(
    *,
    agent_run_id: UUID,
    request_id: str,
    approval_id: str,
    sequence: int,
    payload: JsonObject,
    metadata: JsonObject,
    tool_name: str | None = None,
) -> list[AgentEvent]:
    """A permission request the host answered itself, from an earlier approval.

    Recorded as an approval that is already resolved -- the call and its
    return together -- so the transcript says why the agent was allowed
    without anybody being asked. No STATUS event: that is what makes a surface
    post buttons, and there is nothing left to decide.
    """
    tool_call_id = permission_approval_tool_call_id(request_id)
    return [
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name=CALL_NAMES[PauseKind.HOST_PERMISSION],
                tool_call_id=tool_call_id,
                tool_args=permission_approval_tool_args(
                    payload, request_id=request_id, tool_name=tool_name
                ),
                metadata=metadata,
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name=CALL_NAMES[PauseKind.HOST_PERMISSION],
                tool_result={
                    "success": True,
                    "message": (
                        "Allowed without asking again: the person approved "
                        "this exact action earlier, after the turn that asked "
                        "for it had ended."
                    ),
                    "decision": AgentRunApprovalDecision.APPROVE_ONCE.value,
                    "executed": False,
                    "response": {"approved_earlier": approval_id},
                },
                metadata=metadata,
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
    ]


def _parsed_options(value: object) -> tuple[AgentHostPermissionOption, ...]:
    if not isinstance(value, list):
        return ()
    options: list[AgentHostPermissionOption] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        option_id = _first_string(entry, "optionId", "option_id", "id")
        if option_id is None:
            continue
        options.append(
            AgentHostPermissionOption(
                option_id=option_id,
                kind=_normalized_kind(entry.get("kind")),
                name=_first_string(entry, "name", "label") or "",
            )
        )
    return tuple(options)


def _first_string(source: JsonObject, *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
