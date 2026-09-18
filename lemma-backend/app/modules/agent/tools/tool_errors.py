"""Shared helpers for turning tool-call failures into recoverable responses.

A single tool error must never abort an agent run. Both execution paths use these
helpers so they format and skip errors identically:

  * the in-process LEMMA harness wraps every toolset in ``GracefulToolset`` and
    returns ``format_tool_error`` instead of raising,
  * the remote-harness path (per-conversation / pod MCP services + the approval
    executor)
    catches dispatcher failures and returns the same payload as an MCP tool error
    (``CallToolResult(isError=True, ...)``).

``is_control_flow_exception`` marks the exceptions that are NOT tool failures —
pydantic-ai's retry/deferral/approval/limit signals and task cancellation — which
must always propagate so the framework can act on them.
"""

from __future__ import annotations

import asyncio

from pydantic_ai import ToolReturn
from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.core.errors.describe import describe_exception
from app.core.redaction import redact_text
from app.core.domain.errors import DomainError

# DomainError codes that mean "the agent lacks a grant/approval for this
# action": surface these as ``needs_approval`` so the model can re-issue the
# call through ``request_approval`` instead of treating it as a hard failure.
# DESTRUCTIVE_ACTION_REQUIRES_APPROVAL is the destructive-action gate — the
# user's APPROVE_FOR_SESSION decision unlocks the action type for the rest of
# the conversation.
APPROVAL_CODES = {
    "MISSING_WORKLOAD_RESOURCE_GRANT",
    "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
    "AUTH_REQUIRED",
}


class AgentInputRequired(Exception):
    """Control-flow signal: the run must pause and wait for the user.

    Raised by ``ask_user`` / ``request_approval`` instead of blocking the worker.
    The tool call itself is already persisted, so the harness ends the run cleanly
    (conversation -> WAITING) and the user's submission later starts a fresh run
    that replays the synthesized tool return from history. ``tool_call_id`` is the
    durable approval id; ``kind`` is the tool name that paused.
    """

    def __init__(self, tool_call_id: str, kind: str):
        self.tool_call_id = tool_call_id
        self.kind = kind
        super().__init__(f"Agent run paused for user input ({kind}:{tool_call_id})")


# Exceptions that are control flow, not tool failures: they carry meaning for the
# pydantic-ai run loop (retry / deferral / approval / usage limit / unexpected
# behaviour) or signal cancellation/shutdown. Never swallow these.
_CONTROL_FLOW_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ModelRetry,
    CallDeferred,
    ApprovalRequired,
    AgentInputRequired,
    UsageLimitExceeded,
    UnexpectedModelBehavior,
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


def is_control_flow_exception(exc: BaseException) -> bool:
    """Return True if ``exc`` must propagate rather than become a tool response."""
    return isinstance(exc, _CONTROL_FLOW_EXCEPTIONS)


def safe_error_text(exc: BaseException) -> str:
    """An exception's text with anything secret-shaped stripped out.

    This string goes two places at once: into the model's context, and into the
    durable conversation transcript a person reads. `httpx.HTTPStatusError`
    stringifies with the full URL, so a signed storage link or a connector
    callback carrying a token in its query lands in both; `describe_exception`
    additionally appends the cause, "because for a wrapped transport failure the
    cause is the part that identifies the host, port or timeout involved".

    Logs, telemetry, API exception handlers, connector errors and function
    runtime logs all run their free text through `redact_text`. Agent tools were
    the one surface that did not, and the only one that writes into a transcript
    a user reads back.
    """
    return redact_text(str(exc) or exc.__class__.__name__)


def safe_described_error(exc: BaseException) -> str:
    """`describe_exception`'s text, redacted.

    The cause chain is worth keeping -- for a wrapped transport failure it is the
    part that names the host, port or timeout involved -- which is exactly why it
    needs redacting rather than dropping.
    """
    return redact_text(describe_exception(exc))


def format_tool_error(name: str, exc: BaseException) -> dict[str, object]:
    """Render a tool failure as a structured, model-readable result.

    Returned as a normal tool return so the model sees the error and can adapt
    (retry with different arguments, call another tool, or explain to the user)
    instead of the run terminating.

    The shape matches the uniform tool contract — ``success: False`` plus a
    human-readable ``error`` — so both the frontend and the model see every
    failure the same way, whether it came from a returned response or a raised
    exception caught here. ``error_type``/``tool`` are extra diagnostics.
    """
    return {
        "success": False,
        "error": safe_error_text(exc),
        "error_type": exc.__class__.__name__,
        "tool": name,
    }


def result_is_failure(payload: object) -> bool:
    """Whether a tool's *returned* payload reports a failure.

    Almost nothing here raises. ``GracefulToolset`` turns a raise into
    ``format_tool_error``'s ``success: False`` dict so one bad tool call cannot
    end a run, and the pod and workspace toolsets build the same shape by hand.
    A caller that only watches for exceptions therefore sees every one of those
    failures as a success -- which is how a platform came to believe 4.4% of its
    tool calls failed when the real figure was more than twice that.

    This is the one place that reads the uniform contract's verdict, so the two
    MCP bridges cannot drift apart on what "failed" means.

    ``needs_approval`` is deliberately not a failure. The call stopped because
    the agent has to ask a person first; that is a step in a working flow and
    one the model is built to act on, not something to report as broken.
    """
    verdict, needs_approval = _uniform_contract_verdict(payload)
    if needs_approval:
        return False
    # `is False` rather than falsiness: a payload with no `success` at all is a
    # tool that does not use the uniform contract, not a failed one.
    return verdict is False


def _uniform_contract_verdict(payload: object) -> tuple[object, bool]:
    """``(success, needs_approval)`` read off either shape a tool returns.

    Both are real and neither is going away: the pod and workspace toolsets
    return pydantic ``BaseToolResponse`` models, while ``format_tool_error`` and
    ``approval_error_result`` build plain dicts. Reading only the dict shape
    would have missed most failures, since most tools return the model.
    """
    if isinstance(payload, ToolReturn):
        payload = payload.return_value
    if isinstance(payload, dict):
        return payload.get("success"), bool(payload.get("needs_approval"))
    return getattr(payload, "success", None), bool(
        getattr(payload, "needs_approval", False)
    )


def approval_error_result(
    exc: DomainError, *, tool_name: str, args: dict[str, object]
) -> dict[str, object]:
    """Map a ``DomainError`` to a structured result, flagging grant/authz 403s.

    Most failures become ``success: False`` + ``error``/``code``. When the code is
    one the agent can resolve by requesting access (``APPROVAL_CODES``), add
    ``needs_approval`` plus the ``approval`` envelope so the model can re-issue the
    call through ``request_approval``. Shared by the pod toolset and any other tool
    that reads grant-checked pod resources (e.g. ``view_image``).
    """
    result: dict[str, object] = {
        "success": False,
        "error": exc.message,
        "code": exc.code,
    }
    if exc.code in APPROVAL_CODES:
        result["needs_approval"] = True
        approval: dict[str, object] = {"tool_name": tool_name, "args": args}
        # Carry the denied permission ids (and the deny code) so an
        # APPROVE_FOR_SESSION resolution knows exactly which action types to
        # record in the session-approval store.
        approval["reason_code"] = exc.code
        details = exc.details if isinstance(exc.details, dict) else {}
        permission_ids = details.get("permission_ids")
        if isinstance(permission_ids, list) and permission_ids:
            approval["permission_ids"] = [str(p) for p in permission_ids]
        result["approval"] = approval
    return result
