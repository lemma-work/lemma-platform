"""Request-scoped tool resolution for the ``pod/{id}`` MCP surface.

Mirrors `ConversationMCPService` but is scoped to a pod instead of a
conversation: the pod id is injected from the URL and the pod toolset is exposed
with ``lemma_``-prefixed names. The caller's token determines the authorization
principal; the pod tools then enforce per-resource grants.

Two kinds of token arrive here. A Lemma session -- the Agent Host's, optionally
carrying agent delegation claims -- sees every pod tool. An OAuth access token
held by an outside MCP client (see `mod:mcp_access`) acts as the person who
connected it, and sees only the tools its scopes cover.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from mcp.types import CallToolResult, Tool
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import SuperTokensSessionError

from app.core.authorization.delegation import (
    WorkloadPrincipalType,
    is_pod_default_agent,
    parse_delegation_claims,
)
from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.services.mcp_content import (
    tool_call_error,
    tool_call_result,
)
from app.modules.agent.services.pod_mcp_tool_policy import (
    policy_for,
    scope_for_call,
    without_approval_envelope,
)
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.infrastructure.mcp import (
    exported_tool_name,
    normalize_local_mcp_tool_name,
)
from app.modules.agent.tools.callable_tool_factory import inline_tool_schema_refs
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset
from app.modules.agent.tools.tool_errors import (
    is_control_flow_exception,
)
from app.modules.mcp_access.contracts import (
    McpPrincipal,
    Scope,
    is_mcp_access_token,
)

logger = get_logger(__name__)

_NIL_CONVERSATION_ID = UUID(int=0)


@dataclass(frozen=True, slots=True)
class _Caller:
    ctx: BaseAgentContext
    scopes: frozenset[Scope] | None
    """What an outside client was granted; ``None`` for a Lemma session, which
    is not scoped."""
    principal: McpPrincipal | None = None
    """The outside client and its connection, for the audit line each call
    writes; ``None`` for a Lemma session."""

    def may_call(
        self, tool_name: str, arguments: Mapping[str, object] | None = None
    ) -> bool:
        return (
            self.scopes is None or scope_for_call(tool_name, arguments) in self.scopes
        )


class PodMCPService:
    def __init__(self) -> None:
        self.uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self.dispatcher = AgentToolDispatcher(self.uow_factory)

    async def authorize(self, *, pod_id: UUID, token: str) -> bool:
        caller = await self._caller_from_token(pod_id=pod_id, token=token)
        return caller is not None

    async def list_tools(
        self, *, pod_id: UUID, token: str, principal: McpPrincipal | None = None
    ) -> list[Tool]:
        caller = await self._require_caller(
            pod_id=pod_id, token=token, principal=principal
        )
        tools = await self.dispatcher.list_tools(ctx=caller.ctx, toolsets=[pod_toolset])
        return [
            Tool(
                name=exported_tool_name(tool.name),
                title=policy_for(tool.name).title,
                description=tool.description,
                input_schema=inline_tool_schema_refs(tool.input_schema),
                annotations=policy_for(tool.name).annotations(),
                _meta={"lemma_tool_name": tool.name},
            )
            for tool in tools
            if caller.may_call(tool.name)
        ]

    async def call_tool(
        self,
        *,
        pod_id: UUID,
        token: str,
        name: str,
        arguments: dict[str, Any] | None,
        principal: McpPrincipal | None = None,
    ) -> CallToolResult:
        caller = await self._require_caller(
            pod_id=pod_id, token=token, principal=principal
        )
        tool_name = normalize_local_mcp_tool_name(name)
        if not caller.may_call(tool_name, arguments):
            _audit(caller, tool_name, outcome="refused")
            return tool_call_error(
                tool_name,
                PermissionError(
                    "This connection was not granted "
                    f"{scope_for_call(tool_name, arguments).value}. Reconnect "
                    "it and allow that access to use this."
                ),
            )
        try:
            result = await self.dispatcher.call_tool(
                ctx=caller.ctx,
                toolsets=[pod_toolset],
                name=tool_name,
                arguments=arguments,
            )
        except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
            if is_control_flow_exception(exc):
                raise
            logger.warning(
                "agent.pod_mcp_service.pod_mcp_tool_r_returning.degraded",
                exc_info=True,
            )
            _audit(caller, tool_name, outcome="failed")
            return tool_call_error(tool_name, exc)
        if caller.scopes is not None:
            result = without_approval_envelope(result)
        answer = tool_call_result(result)
        _audit(caller, tool_name, outcome="failed" if answer.is_error else "ok")
        return answer

    async def _require_caller(
        self, *, pod_id: UUID, token: str, principal: McpPrincipal | None = None
    ) -> _Caller:
        if principal is not None:
            # Already verified at the door, this request: `PublicPodMCPApp`
            # checked the token and hands over what it found.
            if principal.pod_id != pod_id:
                raise ValueError("Unauthorized pod MCP token")
            return _external_caller(principal, pod_id=pod_id)
        caller = await self._caller_from_token(pod_id=pod_id, token=token)
        if caller is None:
            raise ValueError("Unauthorized pod MCP token")
        return caller

    async def _caller_from_token(self, *, pod_id: UUID, token: str) -> _Caller | None:
        if is_mcp_access_token(token):
            # An outside client's token is honoured only as the principal the
            # public mount verified -- where its rate limit and Origin check
            # are. Read from a header here, it could be a second
            # `Authorization` the door never looked at.
            return None
        ctx = await self._context_from_session(pod_id=pod_id, token=token)
        return _Caller(ctx=ctx, scopes=None) if ctx is not None else None

    async def _context_from_session(
        self,
        *,
        pod_id: UUID,
        token: str,
    ) -> BaseAgentContext | None:
        try:
            session = await get_session_without_request_response(
                token,
                anti_csrf_check=False,
                session_required=True,
            )
        except SuperTokensSessionError:
            # The token is not valid: expected traffic, and the denial below is
            # the whole answer.
            return None
        except Exception:
            # The auth backend could not answer. Same denial — a caller holding
            # a good token must not be let through because SuperTokens is down —
            # but this is an outage, not a bad token, and catching both as one
            # made the two indistinguishable from outside.
            logger.error(
                "agent.pod_mcp_service.session_lookup.failed",
                exc_info=True,
            )
            return None
        if session is None:
            return None
        user_id = UUID(session.get_user_id())

        workload_id: UUID | None = None
        agent_name: str | None = None
        if settings.authz_delegated_tokens_enabled:
            try:
                claims = parse_delegation_claims(
                    session.get_access_token_payload() or {}
                )
            except Exception:
                claims = None
            if claims is not None and claims.actor_type == WorkloadPrincipalType.AGENT:
                if claims.pod_id != pod_id:
                    return None
                workload_id = claims.actor_id
                agent_name = claims.actor_name

        return _pod_context(
            user_id=user_id,
            pod_id=pod_id,
            workload_id=workload_id,
            agent_name=agent_name,
        )


def _external_caller(principal: McpPrincipal, *, pod_id: UUID) -> _Caller:
    # The person, through the pod's own assistant -- the same standing a
    # session with no delegation claims gets, so row-level security and every
    # grant apply exactly as they do to them.
    return _Caller(
        ctx=_pod_context(user_id=principal.user_id, pod_id=pod_id),
        scopes=principal.scopes,
        principal=principal,
    )


def _audit(
    caller: _Caller, tool_name: str, *, outcome: Literal["ok", "failed", "refused"]
) -> None:
    """One line per outside client's tool call -- including the ones refused
    for scope and the ones that raised: which app, through which connection,
    for whom, did what.

    Records and their events carry the person, not the app, so this line is
    where "that change came from ChatGPT" is written down. The connection, not
    the pod: a grant row outlives its revocation and names the pod, and the
    logging contract allows three identifiers.
    """
    principal = caller.principal
    if principal is None:
        return
    logger.info(
        "agent.pod_mcp_service.external_tool.called",
        tool=tool_name,
        client_id=principal.client_id,
        client_name=principal.client_name[:120],
        grant_id=str(principal.grant_id),
        user_id=str(principal.user_id),
        outcome=outcome,
    )


def _pod_context(
    *,
    user_id: UUID,
    pod_id: UUID,
    workload_id: UUID | None = None,
    agent_name: str | None = None,
) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=_NIL_CONVERSATION_ID,
        workload_type="agent" if workload_id is not None else None,
        workload_id=workload_id,
        agent_name=agent_name,
        is_pod_default_agent=is_pod_default_agent(workload_id, pod_id=pod_id),
        # There is no run here, and so no runtime profile to ask -- this
        # bridge serves an MCP client holding a pod token, not a Lemma agent
        # run. DIRECT rather than the UNAVAILABLE default, because MCP has a
        # first-class image content type and `_mcp_result` already attaches
        # images: the client decides what to do with them, and one that
        # cannot read them still gets the same JSON answer beside them.
        #
        # Left as the default, every image tool told every MCP client that
        # its model could not read images -- including the ones that can --
        # and `_mcp_result`'s image handling could never fire, because no
        # image was ever produced to attach.
        vision_mode=AgentVisionMode.DIRECT,
    )


pod_mcp_service = PodMCPService()
