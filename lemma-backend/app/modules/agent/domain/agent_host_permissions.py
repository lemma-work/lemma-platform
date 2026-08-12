"""ACP permission requests expressed as ordinary Lemma approvals.

An Agent Host run pauses when its ACP agent asks to use a native tool. The host
holds that request open (``desktop/agent-host/src/permissions.rs``) until a decision
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
_REJECT_KINDS = {"rejectonce", "rejectalways"}


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
        """
        if decision == AgentRunApprovalDecision.DENY:
            return None
        allowed = [
            option for option in self.options if option.kind not in _REJECT_KINDS
        ]
        if not allowed:
            return None
        preferred = (
            (_ALLOW_ALWAYS, _ALLOW_ONCE)
            if decision == AgentRunApprovalDecision.APPROVE_FOR_SESSION
            else (_ALLOW_ONCE, _ALLOW_ALWAYS)
        )
        for kind in preferred:
            for option in allowed:
                if option.kind == kind:
                    return option.option_id
        return allowed[0].option_id


def permission_approval_tool_call_id(request_id: str) -> str:
    """Namespace the approval's call id away from the native tool call's own.

    The ACP request id *is* the native tool call id, which the host already
    reported as its own tool call. Reusing it here would make one
    ``tool_call_id`` address two different calls, so the approval gets a
    prefixed id and carries the raw request id in its arguments.
    """
    return f"agent-host-permission:{request_id}"


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

    ``tool_name`` is the name the caller already resolved for the tool call this
    request belongs to, so the card and the call it interrupts say the same
    word. Without one, the payload's own ``kind`` is the fallback — a category
    ("fetch", "execute") rather than a name, which is why the caller resolving
    it properly is preferred.
    """
    tool_call = payload.get("toolCall")
    tool_call = tool_call if isinstance(tool_call, dict) else {}
    title = _first_string(tool_call, "title") or "The local agent needs permission"
    tool_name = tool_name or _first_string(tool_call, "kind", "title") or "native tool"
    reason = _first_string(payload, "message", "reason")
    return {
        "title": title,
        "reason": reason,
        "tool_name": tool_name,
        AGENT_HOST_PERMISSION_KEY: {
            "request_id": request_id,
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
