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
marker this module writes into the call's arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunApprovalDecision,
    JsonObject,
    MessageDraft,
)

# Key under a ``request_approval`` call's args identifying it as an Agent Host
# permission request. Its presence is the single discriminator that routes the
# decision to the host instead of to a resume run.
AGENT_HOST_PERMISSION_KEY = "agent_host_permission"

_ALLOW_ONCE = "allowonce"
_ALLOW_ALWAYS = "allowalways"
# The kinds a decision may select. ACP defines four, and an agent may one day
# offer a fifth: nothing in a new kind's name says what it grants, so it is
# never selected on anyone's behalf -- an approval for once that read a new
# ``allow_forever`` as "not a reject" granted a standing permission. An option
# with no kind at all is the one exception: it is the only choice such an
# agent offers, and refusing it made the approval unanswerable.
_UNLABELLED = ""
_ALLOW_KINDS = (_ALLOW_ONCE, _ALLOW_ALWAYS, _UNLABELLED)


def _normalized_kind(value: object) -> str:
    """Fold ``allow_once`` / ``allowOnce`` / ``ALLOW_ONCE`` to one spelling."""
    return "".join(
        character for character in str(value or "").lower() if character.isalnum()
    )


@dataclass(frozen=True, slots=True)
class AgentHostPermissionOption:
    """One choice the ACP agent offered for its permission request."""

    option_id: str
    kind: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class AgentHostPermissionRequest:
    """A parked ACP permission request, addressed by the id the host waits on."""

    request_id: str
    options: tuple[AgentHostPermissionOption, ...]

    def option_for(self, decision: AgentRunApprovalDecision) -> str | None:
        """The ACP option id to send back, or ``None`` to deny.

        A denial is expressed as "no option selected" rather than by picking a
        reject option: the host answers ``Cancelled`` for it, which is what the
        ACP agent expects when a request goes unapproved. APPROVE_FOR_SESSION
        maps to the agent's own "always" option when it offered one, so the
        agent stops asking for that action for the rest of its session.

        A one-time approval never selects "always". The host caches that
        answer for the rest of the session, so picking it when the agent
        offered nothing narrower turned a single yes into a standing one; it
        denies instead (``refuses_a_one_time_approval``).
        """
        if decision == AgentRunApprovalDecision.DENY:
            return None
        allowed = [option for option in self.options if option.kind in _ALLOW_KINDS]
        if decision != AgentRunApprovalDecision.APPROVE_FOR_SESSION:
            allowed = [option for option in allowed if option.kind != _ALLOW_ALWAYS]
        if not allowed:
            return None
        preferred = (
            (_ALLOW_ALWAYS, _ALLOW_ONCE)
            if decision == AgentRunApprovalDecision.APPROVE_FOR_SESSION
            else (_ALLOW_ONCE,)
        )
        for kind in (*preferred, _UNLABELLED):
            for option in allowed:
                if option.kind == kind:
                    return option.option_id
        return None

    def refuses_a_one_time_approval(self, decision: AgentRunApprovalDecision) -> bool:
        """An approval for once that was denied because only "always" was offered."""
        return (
            decision == AgentRunApprovalDecision.APPROVE_ONCE
            and self.option_for(decision) is None
            and any(option.kind == _ALLOW_ALWAYS for option in self.options)
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
    tool_name = tool_name or _first_string(tool, "name") or "native tool"
    reason = _first_string(payload, "message", "reason")
    # What the agent would run, for the card to show under the marker rather
    # than as ``args``: shown to the person deciding, never executed by Lemma.
    tool_input = payload.get("input")
    return {
        "title": title,
        "reason": reason,
        "tool_name": tool_name,
        AGENT_HOST_PERMISSION_KEY: {
            "request_id": request_id,
            "input": tool_input if isinstance(tool_input, dict) else {},
            "options": [
                {
                    "option_id": option.option_id,
                    "kind": option.kind,
                    "name": option.name,
                }
                for option in _parsed_options(payload.get("options"))
            ],
        },
    }


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
                tool_name="request_approval",
                tool_call_id=tool_call_id,
                tool_args=permission_approval_tool_args(
                    payload,
                    request_id=request_id,
                    tool_name=tool_name,
                ),
                metadata=metadata,
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
        AgentEvent(
            type=AgentEventType.STATUS,
            data={
                "status": "permission_request",
                "kind": "request_approval",
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
    tool_args = permission_approval_tool_args(
        payload, request_id=request_id, tool_name=tool_name
    )
    marker = tool_args[AGENT_HOST_PERMISSION_KEY]
    if isinstance(marker, dict):
        marker[PRE_APPROVED_KEY] = {"approval_id": approval_id}
    return [
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name="request_approval",
                tool_call_id=tool_call_id,
                tool_args=tool_args,
                metadata=metadata,
            ),
            agent_run_id=agent_run_id,
            sequence=sequence,
        ),
        AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_return(
                tool_call_id=tool_call_id,
                tool_name="request_approval",
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


def agent_host_permission_request(
    tool_args: JsonObject,
) -> AgentHostPermissionRequest | None:
    """Read back the marker, or ``None`` for an ordinary ``request_approval``."""
    marker = tool_args.get(AGENT_HOST_PERMISSION_KEY)
    if not isinstance(marker, dict):
        return None
    request_id = marker.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return None
    return AgentHostPermissionRequest(
        request_id=request_id,
        options=_parsed_options(marker.get("options")),
    )


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
