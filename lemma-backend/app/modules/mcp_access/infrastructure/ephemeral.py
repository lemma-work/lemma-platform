"""The two short-lived halves of the authorization-code flow, kept in Redis.

A pending authorization lives from ``/oauth/authorize`` until the person
answers the consent screen; an authorization code from that answer until the
client redeems it. Both are minutes long and single-use, which is what Redis
expiry and ``GETDEL`` already are. In Postgres each would be a table that
grows until something sweeps it.
"""

from __future__ import annotations

import secrets
from typing import Protocol

from pydantic import BaseModel

from app.core.infrastructure.redis.client import get_redis

PENDING_TTL_SECONDS = 600
"""Long enough to sign in from scratch, including an emailed code."""

CODE_TTL_SECONDS = 300
"""RFC 6749 §4.1.2 recommends ten minutes at most; a client redeems in one."""

REGISTRATION_TTL_SECONDS = 24 * 3600
"""A client that is never allowed is forgotten after a day -- registration is
followed by sign-in within minutes; one that comes back later registers again,
which RFC 7591 clients do on `invalid_client`."""

_PENDING_PREFIX = "mcp_access:pending:"
_CLIENT_PREFIX = "mcp_access:client:"
_ONCE_PREFIX = "mcp_access:once:"
_CODE_PREFIX = "mcp_access:code:"


class PendingAuthorization(BaseModel):
    """An authorize request that is waiting for the person to answer."""

    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    code_challenge: str
    scopes: list[str]
    state: str | None
    resource: str
    pod_id: str


class IssuedCode(BaseModel):
    """What an authorization code stands for, until it is redeemed."""

    grant_id: str
    client_id: str
    scopes: list[str]
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    resource: str
    expires_at: float


class RegisteredClient(BaseModel):
    """A dynamically registered client nobody has consented to yet.

    Held here rather than in Postgres: registration is unauthenticated by
    design (RFC 7591), so a row per registration would let anyone grow a table
    without bound. A client is written down only when a person allows it.
    """

    client_id: str
    client_metadata: dict[str, object]
    client_secret_hash: str | None


class _KeyValue(Protocol):
    async def set(self, name: str, value: str, ex: int, nx: bool = False) -> object: ...

    async def get(self, name: str) -> str | None: ...

    async def getdel(self, name: str) -> str | None: ...


class EphemeralStore:
    def __init__(self, redis: _KeyValue | None = None) -> None:
        self._fixed = redis

    @property
    def _redis(self) -> _KeyValue:
        # Resolved per call: the shared client is replaced when it is closed,
        # and a reference kept from construction would outlive it.
        return self._fixed or get_redis()

    async def hold_pending(self, pending: PendingAuthorization) -> str:
        request_id = secrets.token_urlsafe(24)
        await self._redis.set(
            _PENDING_PREFIX + request_id,
            pending.model_dump_json(),
            ex=PENDING_TTL_SECONDS,
        )
        return request_id

    async def read_pending(self, request_id: str) -> PendingAuthorization | None:
        raw = await self._redis.get(_PENDING_PREFIX + request_id)
        return PendingAuthorization.model_validate_json(raw) if raw else None

    async def take_pending(self, request_id: str) -> PendingAuthorization | None:
        """Read and forget, atomically, so one consent answers one request."""
        raw = await self._redis.getdel(_PENDING_PREFIX + request_id)
        return PendingAuthorization.model_validate_json(raw) if raw else None

    async def issue_code(self, issued: IssuedCode) -> str:
        code = secrets.token_urlsafe(32)
        await self._redis.set(
            _CODE_PREFIX + code, issued.model_dump_json(), ex=CODE_TTL_SECONDS
        )
        return code

    async def read_code(self, code: str) -> IssuedCode | None:
        raw = await self._redis.get(_CODE_PREFIX + code)
        return IssuedCode.model_validate_json(raw) if raw else None

    async def take_code(self, code: str) -> IssuedCode | None:
        raw = await self._redis.getdel(_CODE_PREFIX + code)
        return IssuedCode.model_validate_json(raw) if raw else None

    async def hold_client(self, client: RegisteredClient) -> None:
        await self._redis.set(
            _CLIENT_PREFIX + client.client_id,
            client.model_dump_json(),
            ex=REGISTRATION_TTL_SECONDS,
        )

    async def read_client(self, client_id: str) -> RegisteredClient | None:
        raw = await self._redis.get(_CLIENT_PREFIX + client_id)
        return RegisteredClient.model_validate_json(raw) if raw else None

    async def claim_once(self, key: str, ttl_seconds: int) -> bool:
        """True the first time ``key`` is claimed, on any replica, until it
        expires. For values that must be used once, such as an assertion id."""
        claimed = await self._redis.set(
            _ONCE_PREFIX + key, "1", ex=max(1, ttl_seconds), nx=True
        )
        return bool(claimed)
