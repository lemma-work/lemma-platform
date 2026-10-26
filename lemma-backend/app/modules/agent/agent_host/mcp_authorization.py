"""Whether a relayed run token may use a conversation's tools, call by call.

The caller is the Agent Host link (``agent_host_link_mcp``): every ``mcp`` and
``interaction_wait`` frame carries the run's own Lemma token and names the
conversation and run it acts in. The link authenticated the *machine*; this
decides what the *token* may do, and it is asked again on every call, because
everything it checks can change while a run is in flight.

A token is good for a conversation's tools when:

* SuperTokens accepts it, and it belongs to the person the link is paired to.
  A token is minted for one run and sent only to the host leasing it, so one
  arriving on another person's link is a leak being replayed;
* its delegation claims, when it carries any, parse, are not revoked, name an
  agent that still exists in the conversation's pod, and are bound to this
  conversation (``gpy_session_id`` is the conversation id, the key a person's
  "allow for this conversation" is recorded under -- see
  ``WorkspaceSandboxService.get_env_vars``). A token minted before that change
  names the run instead, and is held to the run the call names. Deleting an
  agent revokes its tokens, but the revocation store fails open; the row
  lookup does not;
* the person owns the conversation and is still a member of its pod;
* the run named is one of this conversation's, has not ended, and is leased to
  the host the call arrived from.

A token with no delegation claims is a deployment running with delegated tokens
switched off, and is checked on everything else; a token *with* claims there is
refused, as the HTTP middleware refuses it, because nothing would clamp it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import SuperTokensSessionError

from app.core.authorization.delegation import (
    DelegationClaims,
    DelegationClaimsError,
    WorkloadPrincipalType,
    is_pod_default_agent,
    parse_delegation_claims,
)
from app.core.authorization.delegation_revocation import is_delegation_revoked
from app.core.authorization.dependencies import assert_pod_membership
from app.core.authorization.factory import create_authorization_data_service
from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import TERMINAL_AGENT_RUN_STATUSES
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel

logger = get_logger(__name__)

#: What a caller is refused for on this mount, phrased the way
#: `assert_pod_membership` renders it ("You need access to this pod to ...").
_CONVERSATION_MCP_ACCESS = "use this conversation's tools"


@dataclass(frozen=True, slots=True)
class RelayingHost:
    """The machine a relayed call arrived from, as its link authenticated it."""

    host_id: UUID
    user_id: UUID


@dataclass(frozen=True, slots=True)
class _RunToken:
    user_id: UUID
    claims: DelegationClaims | None


async def authorize_run_token(
    uow_factory: UnitOfWorkFactory,
    *,
    conversation_id: UUID,
    token: str,
    agent_run_id: UUID | None = None,
    host: RelayingHost | None = None,
    allow_ended_run: bool = False,
) -> bool:
    """Whether ``token`` may use ``conversation_id``'s tools as ``agent_run_id``.

    ``allow_ended_run`` is for a parked interaction's wait, which answers a
    run that has ended with its own refusal (``TERMINAL_RUN``) rather than an
    authorization failure the host would retry.
    """
    run_token = await _read_run_token(token)
    if run_token is None:
        return False
    if host is not None and run_token.user_id != host.user_id:
        _denied("host_user_mismatch", conversation_id, agent_run_id)
        return False
    async with uow_factory() as uow:
        conversation = await Conversations(uow).get_conversation(
            conversation_id, include_runs=False
        )
        if conversation is None or conversation.user_id != run_token.user_id:
            return False
        reason = await _refusal(
            uow,
            conversation=conversation,
            run_token=run_token,
            agent_run_id=agent_run_id,
            host=host,
            allow_ended_run=allow_ended_run,
        )
        if reason is not None:
            _denied(reason, conversation_id, agent_run_id)
            return False
        # Owning the conversation is not access to the pod it lives in. Every
        # HTTP conversation route also asserts membership
        # (``CONVERSATION_MEMBERSHIP``), because ownership plus an agent grant
        # survives being removed from a pod -- which is how a removed member
        # kept the ability to instruct that pod's agents (PS-POD-040,
        # DEV-ACCESS-001).
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=run_token.user_id,
            pod_id=conversation.pod_id,
        )
    try:
        assert_pod_membership(auth_ctx, _CONVERSATION_MCP_ACCESS)
    except DomainError:
        logger.warning(
            "agent.conversation_mcp_service.pod_membership_missing.denied",
            conversation_id=str(conversation_id),
        )
        return False
    return True


async def _read_run_token(token: str) -> _RunToken | None:
    """The token's person and delegation claims, or ``None`` to refuse it."""
    try:
        session = await get_session_without_request_response(
            token,
            anti_csrf_check=False,
            session_required=True,
        )
    except SuperTokensSessionError:
        # The token is not valid: expected traffic, and the denial is the whole
        # answer.
        return None
    except Exception:
        # The auth backend could not answer. Same denial — a caller holding a
        # good token must not be let through because SuperTokens is down — but
        # this is an outage, not a bad token, and catching both as one made the
        # two indistinguishable from outside.
        logger.error(
            "agent.conversation_mcp_service.session_lookup.failed",
            exc_info=True,
        )
        return None
    if session is None:
        return None
    try:
        claims = parse_delegation_claims(session.get_access_token_payload() or {})
    except DelegationClaimsError:
        _denied("invalid_delegation_claims", None, None)
        return None
    if claims is not None:
        if not settings.authz_delegated_tokens_enabled:
            _denied("delegated_tokens_disabled", None, None)
            return None
        if await is_delegation_revoked(actor_id=claims.actor_id):
            _denied("delegation_revoked", None, None)
            return None
    return _RunToken(user_id=UUID(session.get_user_id()), claims=claims)


async def _refusal(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation: Conversation,
    run_token: _RunToken,
    agent_run_id: UUID | None,
    host: RelayingHost | None,
    allow_ended_run: bool,
) -> str | None:
    """Why this token may not act in this conversation, or ``None``."""
    claims = run_token.claims
    if claims is not None:
        reason = await _claims_refusal(
            uow, claims=claims, conversation=conversation, agent_run_id=agent_run_id
        )
        if reason is not None:
            return reason
    if agent_run_id is None:
        return None
    run = await Runs(uow).get_agent_run(agent_run_id)
    if run is None or run.conversation_id != conversation.id:
        return "run_not_in_conversation"
    if not allow_ended_run and run.status in TERMINAL_AGENT_RUN_STATUSES:
        return "run_ended"
    if host is not None:
        lease = await uow.session.get(AgentHostRunLeaseModel, agent_run_id)
        if lease is not None and lease.host_id != host.host_id:
            return "run_leased_elsewhere"
    return None


async def _claims_refusal(
    uow: SqlAlchemyUnitOfWork,
    *,
    claims: DelegationClaims,
    conversation: Conversation,
    agent_run_id: UUID | None,
) -> str | None:
    if claims.actor_type is not WorkloadPrincipalType.AGENT:
        return "not_an_agent_token"
    if claims.pod_id != conversation.pod_id:
        return "token_for_another_pod"
    if agent_run_id is not None and claims.session_id not in {
        str(conversation.id),
        str(agent_run_id),
    }:
        return "token_for_another_run"
    if not is_pod_default_agent(claims.actor_id, pod_id=conversation.pod_id):
        # A conversation's and a run's agent are both ON DELETE SET NULL, and
        # null reads as the pod's own assistant -- which acts with the
        # person's full standing. Without this, deleting an agent mid-run
        # promoted every later tool call it made.
        agent = await AgentRepository(uow).get(claims.actor_id)
        if agent is None or agent.pod_id != conversation.pod_id:
            return "agent_removed"
    return None


def _denied(reason: str, conversation_id: UUID | None, run_id: UUID | None) -> None:
    logger.warning(
        "agent.conversation_mcp_service.run_token.denied",
        reason=reason,
        conversation_id=str(conversation_id) if conversation_id else None,
        agent_run_id=str(run_id) if run_id else None,
    )


__all__ = ["RelayingHost", "authorize_run_token"]
