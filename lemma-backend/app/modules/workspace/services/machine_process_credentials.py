"""Which Lemma credential a process started over HTTP carries.

A process on a machine is the caller acting there, so it carries the caller's
authority and never more:

- a person (session, CLI, SDK) gets their own token;
- an agent or a function acting for a person through a delegated token gets a
  token with the *same* delegation claims -- workload, pod, scope, session --
  so it is the same narrowed principal inside the machine as it was outside;
- a function run with nobody behind it gets its own function-run token, never
  the machine creator's session;
- an app's page gets no ``LEMMA_TOKEN`` at all: the page already has the SDK,
  and a token on a machine outlives the page;
- anything else is refused rather than handed an unscoped token.

Every token minted here is an access token with no refresh token behind it,
so it lives for the access-token lifetime (an hour by default) and no longer:
on a pod machine, which is one trust domain, a co-tenant reading it gets that
caller's narrowed authority for at most that long.

This used to call ``get_env_vars(user_id, pod_id)`` with no workload, which
mints a full user session: an app page got its viewer's, an agent's delegated
token became the person's unscoped one, and a person-less function run got
the machine creator's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.workspace.domain.machine_access import CallerKind, MachineCaller
from app.modules.workspace.domain.machine_errors import MachineAccessDenied

if TYPE_CHECKING:
    from app.core.authorization.delegation import DelegationClaims
    from app.core.authorization.function_run import FunctionRunClaims

EnvMinter = Callable[..., Awaitable[dict[str, str]]]


async def process_env_for(
    caller: MachineCaller,
    *,
    machine_pod_id: UUID | None,
    get_env_vars: EnvMinter,
    delegation_claims: DelegationClaims | None,
    function_run_claims: FunctionRunClaims | None,
    function_run_token: str | None,
    delegated_tokens_enabled: bool,
) -> dict[str, str]:
    """The base environment for a process ``caller`` starts on a machine."""
    if caller.kind is CallerKind.APP:
        if caller.user_id is None:
            raise _refused()
        return await get_env_vars(caller.user_id, machine_pod_id, with_token=False)

    if caller.kind is CallerKind.USER:
        if caller.user_id is None or delegation_claims is not None:
            raise _refused()
        return await get_env_vars(caller.user_id, machine_pod_id)

    if caller.user_id is None:
        # A person-less function run: its own token, which reaches only its
        # pod's data and expires with the run.
        if (
            caller.kind is not CallerKind.FUNCTION
            or function_run_claims is None
            or not function_run_token
        ):
            raise _refused()
        from app.core.authorization.function_run import NOBODY_USER_ID

        env = await get_env_vars(
            NOBODY_USER_ID, function_run_claims.pod_id, with_token=False
        )
        env.pop("LEMMA_USER_ID", None)
        env["LEMMA_TOKEN"] = function_run_token
        return env

    # An agent or function acting for a person: the same claims, never wider.
    if delegation_claims is None or not delegated_tokens_enabled:
        raise _refused()
    return await get_env_vars(
        caller.user_id,
        delegation_claims.pod_id,
        workload_type=str(delegation_claims.actor_type.value),
        workload_id=delegation_claims.actor_id,
        workload_name=delegation_claims.actor_name,
        scope=list(delegation_claims.scope),
        session_id=delegation_claims.session_id,
        conversation_id=caller.conversation_id,
    )


def _refused() -> MachineAccessDenied:
    return MachineAccessDenied(
        "this machine", "start a process with your credentials on"
    )


__all__ = ["process_env_for"]
