"""Which Lemma credential a process started over HTTP carries.

A process on a machine is the caller acting there, so it carries the caller's
authority and never more:

- a person (session, CLI, SDK) gets their own token on their own machine,
  where nobody else runs anything. On a pod machine -- one trust domain, where
  every co-tenant can read every process's environment -- they get a token
  pinned to that pod instead: minted as the pod's own agent acting for them,
  the same delegated token that agent's runs carry, so it reaches what they
  may do in that pod and nothing in any other pod or the organization;
- an agent or a function acting for a person through a delegated token gets a
  token with the *same* delegation claims -- workload, pod, scope, session --
  so it is the same narrowed principal inside the machine as it was outside;
- a function run with nobody behind it gets its own function-run token, never
  the machine creator's session;
- an app's page starts nothing: its script is the app author's, acting with
  its viewer's whole access, and a process it started would be that author's
  code running as the viewer beside everybody else's on the machine;
- anything else is refused rather than handed an unscoped token.

Every token minted here is an access token with no refresh token behind it,
so it lives for the access-token lifetime (an hour by default) and no longer:
on a pod machine, which is one trust domain, a co-tenant reading it gets that
caller's authority in that pod for at most that long.

This used to call ``get_env_vars(user_id, pod_id)`` with no workload, which
mints a full user session: an app page got its viewer's, an agent's delegated
token became the person's unscoped one, and a person-less function run got
the machine creator's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.modules.workspace.domain.machine_access import CallerKind, MachineCaller
from app.modules.workspace.domain.machine_errors import MachineAccessDenied
from app.modules.workspace.services.machine_services import TokenClaims

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
) -> tuple[dict[str, str], TokenClaims | None]:
    """The base environment for a process ``caller`` starts on a machine, and
    the claims its token was minted for -- None when it carries no token Lemma
    can mint again (a person-less function run), so a service restarted later
    gets none either, never a wider one.

    ``machine_pod_id`` is the pod whose machine this is, None for a person's
    own: whether anybody else runs anything there."""
    if caller.kind is CallerKind.APP:
        raise MachineAccessDenied(
            "this machine", "start a process from an app's page on"
        )

    if caller.kind is CallerKind.USER:
        if caller.user_id is None or delegation_claims is not None:
            raise _refused()
        if machine_pod_id is None:
            env = await get_env_vars(caller.user_id, None)
            return env, TokenClaims(user_id=caller.user_id)
        return await _pinned_to_the_pod(
            caller.user_id,
            machine_pod_id,
            get_env_vars=get_env_vars,
            delegated_tokens_enabled=delegated_tokens_enabled,
        )

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
        return env, None

    # An agent or function acting for a person: the same claims, never wider.
    if delegation_claims is None or not delegated_tokens_enabled:
        raise _refused()
    claims = TokenClaims(
        user_id=caller.user_id,
        pod_id=delegation_claims.pod_id,
        workload_type=str(delegation_claims.actor_type.value),
        workload_id=delegation_claims.actor_id,
        workload_name=delegation_claims.actor_name,
        scope=tuple(delegation_claims.scope),
        conversation_id=caller.conversation_id,
        session_id=delegation_claims.session_id,
    )
    env = await get_env_vars(
        claims.user_id,
        claims.pod_id,
        workload_type=claims.workload_type,
        workload_id=claims.workload_id,
        workload_name=claims.workload_name,
        scope=list(delegation_claims.scope),
        session_id=claims.session_id,
        conversation_id=claims.conversation_id,
    )
    return env, claims


async def _pinned_to_the_pod(
    user_id: UUID,
    pod_id: UUID,
    *,
    get_env_vars: EnvMinter,
    delegated_tokens_enabled: bool,
) -> tuple[dict[str, str], TokenClaims]:
    """A person's token on a pod machine: the pod's own agent acting for them.

    Not their own session. Everything on a pod machine reads everything else's
    environment, so a person's whole session there -- every pod and the
    organization, for an hour -- was any co-tenant's: an editor's, an agent
    run steered by text it read, an app page's. Pinned to the pod, it is what
    that person may do in the pod the co-tenants already share, and the
    destructive actions any delegated token must have approved first.

    With delegated tokens off there is no pinned token to mint, and the
    person's own would be the leak above: refused, as an agent's is.
    """
    from app.core.authorization.delegation import (
        DEFAULT_POD_AGENT_NAME,
        WorkloadPrincipalType,
    )

    if not delegated_tokens_enabled:
        raise _refused()
    claims = TokenClaims(
        user_id=user_id,
        pod_id=pod_id,
        workload_type=WorkloadPrincipalType.AGENT.value,
        # The pod's own agent is known by its pod's id.
        workload_id=pod_id,
        workload_name=DEFAULT_POD_AGENT_NAME,
        # Its own delegation session, kept with a service so a restart's
        # token is the same principal.
        session_id=str(uuid4()),
    )
    env = await get_env_vars(
        user_id,
        pod_id,
        workload_type=claims.workload_type,
        workload_id=claims.workload_id,
        workload_name=claims.workload_name,
        session_id=claims.session_id,
    )
    return env, claims


def _refused() -> MachineAccessDenied:
    return MachineAccessDenied(
        "this machine", "start a process with your credentials on"
    )


__all__ = ["process_env_for"]
