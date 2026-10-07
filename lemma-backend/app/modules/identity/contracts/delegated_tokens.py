"""Access tokens minted for a workload acting on a user's behalf.

Replaces the two token functions in `app/composition/workspace_identity.py`,
which differed only in what they returned and duplicated the claim-building
between them. What a caller supplies is a workload -- a sandbox session, a
function run -- and what it gets back is a token carrying that workload's
delegation claims, or a plain impersonation token when delegation is off or the
workload is not pod-scoped.

**Two operations, not one.** The expiry is not free: SuperTokens reports it from
`get_session_information`, a second round trip to the core after the session is
minted. A caller that uses the token immediately must not pay for it, and a
caller that caches the token must not guess at it -- a local TTL that outlives
the issuer's is a token cache handing out dead tokens. So the choice is at the
call site, named.

`DelegatedToken` rather than the caller's own token type: the composition file
imported `function`'s `FunctionSessionToken` to build one, which pointed identity
at a module it has no business knowing. The caller converts.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
authentication infrastructure, and everything importing any identity contract
would otherwise pay for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.config import settings
from app.modules.identity.infrastructure.supertokens_auth.helpers import (
    create_delegated_session,
    get_user_token,
    get_user_token_with_expiry,
)
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)


@dataclass(frozen=True, slots=True)
class DelegatedToken:
    """A minted access token and the issuer's own expiry for it."""

    value: str
    expires_at: datetime


def _delegation_claims(
    *,
    user_id: UUID,
    workload_type: str | None,
    workload_id: UUID | None,
    pod_id: UUID | None,
    session_id: str,
    workload_name: str | None,
    scope: list[str] | None,
    delegated_tokens_enabled: bool,
) -> dict[str, object] | None:
    """The workload's claims, or ``None`` when this token cannot carry any.

    All three of workload type, workload id and pod id are required together:
    a delegation claim that cannot name what is acting, or the pod it is acting
    in, is not a narrowing of the token -- it is an unscoped impersonation with
    extra fields.
    """
    if not (
        delegated_tokens_enabled
        and workload_type
        and workload_id is not None
        and pod_id is not None
    ):
        return None
    return build_delegation_claims(
        workload_type=workload_type,
        workload_id=workload_id,
        pod_id=pod_id,
        session_id=session_id,
        invoked_by_user_id=user_id,
        workload_name=workload_name,
        scope=scope,
    )


async def mint_delegated_token(
    *,
    user_id: UUID,
    workload_type: str | None,
    workload_id: UUID | None,
    pod_id: UUID | None,
    session_id: str,
    workload_name: str | None,
    scope: list[str] | None,
    delegated_tokens_enabled: bool,
) -> str:
    """A token for this workload, for a caller that spends it straight away."""
    return await get_user_token(
        user_id,
        delegation_claims=_delegation_claims(
            user_id=user_id,
            workload_type=workload_type,
            workload_id=workload_id,
            pod_id=pod_id,
            session_id=session_id,
            workload_name=workload_name,
            scope=scope,
            delegated_tokens_enabled=delegated_tokens_enabled,
        ),
    )


async def mint_delegated_token_with_expiry(
    *,
    user_id: UUID,
    workload_type: str | None,
    workload_id: UUID | None,
    pod_id: UUID | None,
    session_id: str,
    workload_name: str | None,
    scope: list[str] | None,
    delegated_tokens_enabled: bool,
) -> DelegatedToken:
    """The same token, plus the issuer's expiry, for a caller that keeps it."""
    issued = await get_user_token_with_expiry(
        user_id,
        delegation_claims=_delegation_claims(
            user_id=user_id,
            workload_type=workload_type,
            workload_id=workload_id,
            pod_id=pod_id,
            session_id=session_id,
            workload_name=workload_name,
            scope=scope,
            delegated_tokens_enabled=delegated_tokens_enabled,
        ),
    )
    return DelegatedToken(value=issued.value, expires_at=issued.expires_at)


@dataclass(frozen=True, slots=True)
class DelegatedSession:
    """A token for the pod's own agent acting for a person, its real expiry,
    and the handle of the session behind it."""

    value: str
    expires_at: datetime
    session_handle: str


async def mint_pod_agent_session(
    *, user_id: UUID, pod_id: UUID, session_id: str
) -> DelegatedSession:
    """A token with which the pod's own agent acts as this person, in this pod.

    The standing a pod's MCP tools already run with: the person's roles and
    row-level security, refused for any other pod and for organization-level
    actions, destructive actions gated. The actor name stays unset on purpose --
    any name but the assistant's demotes the token to a named workload with only
    its own grants -- so who minted it rides on ``session_id``.

    Refuses rather than degrading when delegated tokens are switched off: the
    fallback there is a plain impersonation token, which no caller of this
    should ever hand out.
    """
    claims = _delegation_claims(
        user_id=user_id,
        workload_type="AGENT",
        workload_id=pod_id,
        pod_id=pod_id,
        session_id=session_id,
        workload_name=None,
        scope=None,
        delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
    )
    if claims is None:
        raise RuntimeError("Delegated tokens are disabled on this deployment")
    issued = await create_delegated_session(user_id, claims)
    return DelegatedSession(
        value=issued.value,
        expires_at=issued.expires_at,
        session_handle=issued.session_handle,
    )


__all__ = [
    "DelegatedSession",
    "DelegatedToken",
    "mint_delegated_token",
    "mint_delegated_token_with_expiry",
    "mint_pod_agent_session",
]
