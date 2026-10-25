"""Refusing to start a run the account has no headroom for.

Every way a run begins has to ask: a new message, the follow-up turn for a
queued one, a retry, and the run that resumes a pause. One that skipped it
would be a free way past a limit the others are held to, so the check lives
here rather than on whichever of them happened to need it first.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import UsageService

#: What each limit window is called when it is the one that refused a run.
_LIMIT_NAMES = (
    ("org_month", "organization monthly"),
    ("user_week", "user weekly"),
    ("user_month", "user monthly"),
    ("contacts_month", "contacts monthly"),
)


async def assert_usage_preflight_allowed(
    usage_service: UsageService | None,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    agent_runtime: AgentRuntimeConfig,
    outside_audience: bool,
) -> None:
    """Raise ``UsageLimitExceededError`` when the run must not start.

    ``outside_audience``: the run answers somebody outside the organization
    (``Audience.answers_outsiders``), so it is held to the organization's
    limits and its contacts cap, not to the personal limits of ``user_id``.
    """
    if usage_service is None:
        return
    if not agent_runtime.profile_id.startswith("system:"):
        return
    spent = await usage_service.spent_windows(
        organization_id=organization_id,
        user_id=user_id,
        outside_audience=outside_audience,
    )
    if not spent:
        return
    # Say which limit was reached, not just that one was: a person told
    # only "limit exceeded" cannot tell whether to wait for the month to
    # turn or to ask an admin for headroom. See PS-OPS-012.
    raise UsageLimitExceededError(
        "LLM usage limit exceeded: "
        + ", ".join(
            f"{name} limit reached" for kind, name in _LIMIT_NAMES if kind in spent
        )
    )
