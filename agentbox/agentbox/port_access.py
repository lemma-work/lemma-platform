from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from urllib.parse import urlsplit
from uuid import UUID

from agentbox.domain import (
    AgentBoxError,
    AllocationState,
    ErrorCode,
    PortAccessClaims,
    PortAccessGrant,
    PortProtocol,
    RetryDisposition,
    SandboxKey,
    SandboxProfileRef,
    WorkloadKind,
)
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderMetadataEntry,
    ProviderPortAccessPort,
    ProviderPortTarget,
)

_HTTP_HEADER_TOKEN = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
_RESERVED_RUNTIME_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "host",
        "if-match",
        "prefer",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-lemma-gateway-url",
    }
)


@dataclass(frozen=True, slots=True)
class PortAccessSigner:
    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("port-access signing key must be at least 32 bytes")

    def issue(self, claims: PortAccessClaims) -> str:
        payload = "\x1f".join(
            (
                "1",
                claims.key.workload_kind.value,
                str(claims.key.logical_id),
                str(claims.allocation_id),
                str(claims.allocation_epoch),
                str(claims.port),
                claims.protocol.value,
                str(int(claims.expires_at.timestamp())),
            )
        ).encode()
        signature = hmac.new(self.key, payload, hashlib.sha256).digest()
        return f"{self._encode(payload)}.{self._encode(signature)}"

    def verify(self, token: str) -> PortAccessClaims:
        try:
            payload_text, signature_text = token.split(".", 1)
            payload = self._decode(payload_text)
            signature = self._decode(signature_text)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("port-access token is malformed") from exc
        expected = hmac.new(self.key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("port-access token signature is invalid")
        try:
            (
                version,
                workload_kind,
                logical_id,
                allocation_id,
                allocation_epoch,
                port,
                protocol,
                expires_at,
            ) = payload.decode().split("\x1f")
            if version != "1":
                raise ValueError("unsupported token version")
            claims = PortAccessClaims(
                key=SandboxKey(
                    workload_kind=WorkloadKind(workload_kind),
                    logical_id=UUID(logical_id),
                ),
                allocation_id=UUID(allocation_id),
                allocation_epoch=int(allocation_epoch),
                port=int(port),
                protocol=PortProtocol(protocol),
                expires_at=datetime.fromtimestamp(int(expires_at), tz=timezone.utc),
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("port-access token claims are invalid") from exc
        if claims.expires_at <= datetime.now(timezone.utc):
            raise ValueError("port-access token has expired")
        if not 1 <= claims.port <= 65535 or claims.allocation_epoch < 1:
            raise ValueError("port-access token claims are out of range")
        return claims

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
        # Base64url without padding has multiple textual aliases when unused
        # trailing bits are non-zero. Accepting an alias would allow a signed
        # URL to be textually modified without changing its decoded signature.
        if PortAccessSigner._encode(decoded) != value:
            raise ValueError("port-access token encoding is not canonical")
        return decoded


@dataclass(frozen=True, slots=True)
class FunctionRuntimeEndpointLease:
    """Allocation-fenced direct endpoint for one resident function runtime."""

    key: SandboxKey
    allocation_id: UUID
    allocation_epoch: int
    profile: SandboxProfileRef
    url: str
    request_headers: tuple[ProviderMetadataEntry, ...] = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("function runtime endpoint must be an HTTP(S) URL")
        if self.key.workload_kind != WorkloadKind.FUNCTION:
            raise ValueError("function runtime lease requires a function sandbox")
        if self.allocation_epoch < 1:
            raise ValueError("function runtime allocation epoch must be positive")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("function runtime lease expiry must include a timezone")
        names: set[str] = set()
        for header in self.request_headers:
            normalized = header.name.lower()
            if (
                not header.name
                or any(character not in _HTTP_HEADER_TOKEN for character in header.name)
                or normalized in _RESERVED_RUNTIME_HEADERS
                or normalized in names
            ):
                raise ValueError("function runtime provider header is invalid")
            if "\r" in header.value or "\n" in header.value or "\x00" in header.value:
                raise ValueError("function runtime provider header value is invalid")
            names.add(normalized)


class PortAccessService:
    def __init__(
        self,
        database: StateDatabase,
        provider: ProviderPortAccessPort,
        signer: PortAccessSigner,
        *,
        public_base_url: str,
        trusted_function_activity_seconds: float = 300,
        trusted_function_activity_refresh_seconds: float = 60,
    ) -> None:
        if not 1 <= trusted_function_activity_seconds <= 24 * 60 * 60:
            raise ValueError(
                "trusted function activity lease must be in 1..86400 seconds"
            )
        if not 1 <= trusted_function_activity_refresh_seconds <= 60 * 60:
            raise ValueError(
                "trusted function activity refresh must be in 1..3600 seconds"
            )
        self._database = database
        self._provider = provider
        self._signer = signer
        self._public_base_url = public_base_url.rstrip("/")
        self._trusted_function_activity = timedelta(
            seconds=trusted_function_activity_seconds
        )
        self._trusted_function_activity_refresh = timedelta(
            seconds=trusted_function_activity_refresh_seconds
        )

    async def create(
        self,
        key: SandboxKey,
        *,
        port: int,
        protocol: PortProtocol,
        expires_at: datetime,
    ) -> PortAccessGrant:
        now = datetime.now(timezone.utc)
        if not 1 <= port <= 65535:
            raise self._invalid("port must be in 1..65535")
        if key.workload_kind == WorkloadKind.FUNCTION and port != 8090:
            raise AgentBoxError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "function profile exposes only the resident runtime port",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        if expires_at.tzinfo is None or expires_at <= now:
            raise self._invalid("expires_at must be a future absolute timestamp")
        maximum_lifetime = (
            timedelta(hours=24)
            if key.workload_kind == WorkloadKind.FUNCTION
            else timedelta(hours=1)
        )
        if expires_at > now + maximum_lifetime:
            raise self._invalid(
                "function runtime access cannot exceed 24 hours"
                if key.workload_kind == WorkloadKind.FUNCTION
                else "port access cannot exceed one hour"
            )
        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(key)
            allocation = await uow.repository.current_allocation(key)
            if (
                logical is None
                or allocation is None
                or allocation.provider_id is None
                or allocation.state != AllocationState.ACTIVE
                or logical.allocation_epoch < 1
            ):
                raise AgentBoxError(
                    ErrorCode.PROVISIONING,
                    "sandbox is not ready for port access",
                    retry=RetryDisposition.WAIT,
                    status_code=409,
                )
            logical = await uow.repository.protect_port_access(
                key, until=expires_at, now=now
            )
            await uow.commit()
        claims = PortAccessClaims(
            key=key,
            allocation_id=allocation.allocation_id,
            allocation_epoch=logical.allocation_epoch,
            port=port,
            protocol=protocol,
            expires_at=expires_at,
        )
        token = self._signer.issue(claims)
        return PortAccessGrant(
            key=key,
            port=port,
            protocol=protocol,
            url=f"{self._public_base_url}/port-access/{token}/",
            expires_at=expires_at,
        )

    async def resolve(
        self, token: str, *, deadline_at: datetime
    ) -> tuple[PortAccessClaims, ProviderPortTarget]:
        try:
            claims = self._signer.verify(token)
        except ValueError as exc:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                str(exc),
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=403,
            ) from exc
        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(claims.key)
            allocation = await uow.repository.current_allocation(claims.key)
            await uow.commit()
        if (
            logical is None
            or allocation is None
            or allocation.provider_id is None
            or allocation.state != AllocationState.ACTIVE
            or allocation.allocation_id != claims.allocation_id
            or logical.allocation_epoch != claims.allocation_epoch
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "port-access grant belongs to a stale allocation",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=410,
            )
        target = await self._provider.resolve_port_target(
            ProviderAllocationRef(
                provider_id=allocation.provider_id,
                provider_instance_id=allocation.provider_instance_id,
                allocation_id=allocation.allocation_id,
                allocation_token=allocation.allocation_token,
                key=allocation.key,
            ),
            port=claims.port,
            protocol=claims.protocol,
            deadline_at=deadline_at,
            activity_until=claims.expires_at,
        )
        return claims, target

    async def lease_function_runtime(
        self,
        logical_id: UUID,
        *,
        deadline_at: datetime,
        required_valid_until: datetime | None = None,
    ) -> FunctionRuntimeEndpointLease:
        """Lease a provider-neutral direct endpoint to the current allocation."""

        key = SandboxKey(
            workload_kind=WorkloadKind.FUNCTION,
            logical_id=logical_id,
        )
        now = datetime.now(timezone.utc)
        if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
            raise self._invalid("deadline_at must include a timezone")
        if required_valid_until is not None and (
            required_valid_until.tzinfo is None
            or required_valid_until.utcoffset() is None
        ):
            raise self._invalid("required_valid_until must include a timezone")
        minimum_valid_until = now + self._trusted_function_activity
        required_valid_until = max(
            minimum_valid_until,
            required_valid_until or minimum_valid_until,
        )
        maximum_valid_until = now + timedelta(hours=24)
        if required_valid_until > maximum_valid_until:
            raise self._invalid("required_valid_until cannot exceed 24 hours")
        lease_until = min(
            required_valid_until + self._trusted_function_activity_refresh,
            maximum_valid_until,
        )
        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(key, for_update=True)
            if logical is None:
                raise AgentBoxError(
                    ErrorCode.SANDBOX_NOT_FOUND,
                    "function sandbox does not exist",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=404,
                )
            allocation = await uow.repository.current_allocation(key)
            if (
                allocation is None
                or allocation.provider_id is None
                or allocation.state != AllocationState.ACTIVE
                or allocation.allocation_epoch is None
                or allocation.allocation_epoch != logical.allocation_epoch
                or allocation.profile != logical.profile
            ):
                raise AgentBoxError(
                    ErrorCode.PROVISIONING,
                    "function sandbox is not ready for direct runtime access",
                    retry=RetryDisposition.WAIT,
                    status_code=409,
                    retry_after_ms=250,
                )
            logical = await uow.repository.protect_activity(
                key,
                until=lease_until,
                now=now,
            )
            await uow.commit()
        target = await self._provider.resolve_port_target(
            ProviderAllocationRef(
                provider_id=allocation.provider_id,
                provider_instance_id=allocation.provider_instance_id,
                allocation_id=allocation.allocation_id,
                allocation_token=allocation.allocation_token,
                key=allocation.key,
            ),
            port=8090,
            protocol=PortProtocol.HTTP,
            deadline_at=deadline_at,
            activity_until=lease_until,
        )
        return FunctionRuntimeEndpointLease(
            key=key,
            allocation_id=allocation.allocation_id,
            allocation_epoch=allocation.allocation_epoch,
            profile=logical.profile,
            url=target.base_url.rstrip("/") + "/",
            request_headers=target.headers,
            expires_at=lease_until,
        )

    @staticmethod
    def _invalid(message: str) -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.INVALID_REQUEST,
            message,
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=422,
        )
