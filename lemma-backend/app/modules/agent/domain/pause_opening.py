"""What a tool call opens a pause with, carried on the call's draft.

A leaf, like ``state_kinds``: the draft a harness produces names it, so this
imports neither the draft nor the entities. The writer that persists the call
opens the ``Pause`` from it in the same transaction (``domain/pauses``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.state_kinds import AgentRunApprovalDecision, PauseKind

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


class AgentHostPermissionOption(BaseModel):
    """One choice the ACP agent offered for its permission request."""

    model_config = ConfigDict(frozen=True)

    option_id: str
    kind: str
    name: str = ""


class AgentHostPermissionRequest(BaseModel):
    """A parked ACP permission request, addressed by the id the host waits on.

    ``input`` and ``tool_name`` are what the agent would run: shown to the
    person deciding, carried into a resumed turn as a pre-approval, never run
    by Lemma.
    """

    model_config = ConfigDict(frozen=True)

    request_id: str
    options: tuple[AgentHostPermissionOption, ...] = ()
    input: dict[str, object] = Field(default_factory=dict)
    tool_name: str | None = None

    def option_for(self, decision: AgentRunApprovalDecision) -> str | None:
        """The ACP option id to send back, or ``None`` to deny.

        A denial is "no option selected": the host answers ``Cancelled``, which
        is what the ACP agent expects of an unapproved request.
        APPROVE_FOR_SESSION maps to the agent's own "always" option when it
        offered one. A one-time approval never selects "always" -- the host
        caches that for the rest of the session -- so it denies instead
        (``refuses_a_one_time_approval``).
        """
        if decision == AgentRunApprovalDecision.DENY:
            return None
        allowed = [option for option in self.options if option.kind in _ALLOW_KINDS]
        if decision != AgentRunApprovalDecision.APPROVE_FOR_SESSION:
            allowed = [option for option in allowed if option.kind != _ALLOW_ALWAYS]
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


class BudgetSpent(BaseModel):
    """Which of a run's allowances ran out, and how much it had spent."""

    model_config = ConfigDict(frozen=True)

    dimension: str
    spent: int


class PauseOpening(BaseModel):
    """The pause a tool call opens: its kind, and what that kind needs."""

    model_config = ConfigDict(frozen=True)

    kind: PauseKind
    host_permission: AgentHostPermissionRequest | None = None
    budget: BudgetSpent | None = None


__all__ = [
    "AgentHostPermissionOption",
    "AgentHostPermissionRequest",
    "BudgetSpent",
    "PauseOpening",
]
