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
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal
from uuid import UUID

from mcp.types import CallToolResult, Tool
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)
from supertokens_python.recipe.session.exceptions import SuperTokensSessionError

from app.core.authorization.delegation import (
    DelegationClaims,
    WorkloadPrincipalType,
    is_default_pod_agent_claims,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.security import (
    PrincipalLookups,
    PrincipalRefused,
    resolve_delegated_principal,
)
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.services.mcp_content import (
    tool_call_error,
    tool_call_result,
)
from app.modules.agent.services.pod_mcp_apps import (
    app_tools,
    call_app_tool,
    is_app_tool,
    offers_apps,
    openable_apps,
)
from app.modules.agent.services.pod_mcp_tool_policy import (
    policy_for,
    scope_for_call,
    without_approval_envelope,
)
from app.modules.agent.infrastructure.mcp import (
    exported_tool_name,
    normalize_local_mcp_tool_name,
)
from app.modules.agent.tools.callable_tool_factory import inline_tool_schema_refs
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher, ToolInfo
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


@dataclass(frozen=True, slots=True)
class VerifiedSession:
    """A Lemma session whose signature checked out: whose it is, and its claims."""

    user_id: UUID
    payload: Mapping[str, object]


SessionVerifier = Callable[[str], Awaitable[VerifiedSession | None]]


async def verify_supertokens_session(token: str) -> VerifiedSession | None:
    """The session ``token`` names, or ``None`` when it names none."""
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
            "agent.pod_mcp_service.session_lookup.failed",
            exc_info=True,
        )
        return None
    if session is None:
        return None
    return VerifiedSession(
        user_id=UUID(session.get_user_id()),
        payload=session.get_access_token_payload() or {},
    )


class PodMCPService:
    def __init__(
        self,
        *,
        verify_session: SessionVerifier = verify_supertokens_session,
        principal_lookups: PrincipalLookups | None = None,
    ) -> None:
        self.uow_factory = SessionUnitOfWorkFactory(async_session_maker)
        self.dispatcher = AgentToolDispatcher(self.uow_factory)
        self._verify_session = verify_session
        self._principal_lookups = principal_lookups

    async def authorize(self, *, pod_id: UUID, token: str) -> bool:
        caller = await self._caller_from_token(pod_id=pod_id, token=token)
        return caller is not None

    async def context_for(self, *, pod_id: UUID, token: str) -> BaseAgentContext | None:
        """The tool context a Lemma session token gets on this pod, if any."""
        caller = await self._caller_from_token(pod_id=pod_id, token=token)
        return caller.ctx if caller is not None else None

    async def list_tools(
        self, *, pod_id: UUID, token: str, principal: McpPrincipal | None = None
    ) -> list[Tool]:
        caller = await self._require_caller(
            pod_id=pod_id, token=token, principal=principal
        )
        tools = await self.dispatcher.list_tools(ctx=caller.ctx, toolsets=[pod_toolset])
        listed = [as_mcp_tool(tool) for tool in tools if caller.may_call(tool.name)]
        if caller.principal is not None and offers_apps(caller.principal):
            apps = await openable_apps(
                self.uow_factory, pod_id=pod_id, user_id=caller.principal.user_id
            )
            listed += app_tools(apps)
        return listed

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
        if is_app_tool(name):
            return await self._call_app_tool(caller, name, arguments, pod_id=pod_id)
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

    async def _call_app_tool(
        self,
        caller: _Caller,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        pod_id: UUID,
    ) -> CallToolResult:
        if caller.principal is None or not offers_apps(caller.principal):
            _audit(caller, name, outcome="refused")
            return tool_call_error(
                name,
                PermissionError(
                    "Apps open only on a connection allowed to change things. "
                    "Reconnect it and allow that to open apps here."
                ),
            )
        result = await call_app_tool(
            self.uow_factory,
            name,
            dict(arguments or {}),
            pod_id=pod_id,
            principal=caller.principal,
        )
        _audit(caller, name, outcome="failed" if result.is_error else "ok")
        return result

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
        session = await self._verify_session(token)
        if session is None:
            return None
        try:
            # The same resolver the HTTP dependency runs: these mounts are ASGI
            # apps it never sees, so this is the only gate a token passes here.
            claims = await resolve_delegated_principal(
                session.payload,
                user_id=session.user_id,
                pod_id=pod_id,
                lookups=self._principal_lookups,
            )
        except PrincipalRefused as refused:
            logger.warning(
                "agent.pod_mcp_service.token.refused",
                code=refused.code,
                pod_id=str(pod_id),
            )
            return None
        return _pod_context_for(session.user_id, pod_id=pod_id, claims=claims)


def as_mcp_tool(tool: ToolInfo) -> Tool:
    """A pod tool as an MCP client is shown it: annotated, and linked to its view."""
    policy = policy_for(tool.name)
    meta: dict[str, object] = {"lemma_tool_name": tool.name}
    if policy.view is not None:
        # Named for every caller, not only those that said they render MCP
        # Apps: this server is stateless, so a client's `initialize`
        # capabilities are gone by the time it lists tools. A host that cannot
        # draw the view ignores the key, and the text result is whole without it.
        meta.update(policy.view.tool_meta())
    return Tool(
        name=exported_tool_name(tool.name),
        title=policy.title,
        description=tool.description,
        input_schema=inline_tool_schema_refs(tool.input_schema),
        annotations=policy.annotations(),
        _meta=meta,
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


def _pod_context_for(
    user_id: UUID, *, pod_id: UUID, claims: DelegationClaims | None
) -> BaseAgentContext:
    """Every delegated token is held to its own workload's authority.

    Only the pod's own assistant acts with the person's roles. A named agent
    gets its grants; a function gets the function's, never the person's.
    """
    if claims is None:
        return _pod_context(user_id=user_id, pod_id=pod_id)
    scope = frozenset(claims.scope)
    if claims.actor_type is WorkloadPrincipalType.FUNCTION:
        return _pod_context(
            user_id=user_id,
            pod_id=pod_id,
            workload_type="function",
            workload_id=claims.actor_id,
            is_pod_default_agent=False,
            delegation_scope=scope,
        )
    return _pod_context(
        user_id=user_id,
        pod_id=pod_id,
        workload_type="agent",
        workload_id=claims.actor_id,
        agent_name=claims.actor_name,
        is_pod_default_agent=is_default_pod_agent_claims(claims),
        delegation_scope=scope,
    )


def _pod_context(**fields: Any) -> BaseAgentContext:
    """The run session's pod-scoped variant; imported on first use, so the run
    session's graph stays out of the API's startup imports."""
    from app.modules.agent.services.run_session import pod_tool_context

    return pod_tool_context(**fields)


pod_mcp_service = PodMCPService()
