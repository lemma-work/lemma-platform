from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
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
    WorkloadKind,
)
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderPortAccessPort,
    ProviderPortTarget,
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


class PortAccessService:
    def __init__(
        self,
        database: StateDatabase,
        provider: ProviderPortAccessPort,
        signer: PortAccessSigner,
        *,
        public_base_url: str,
    ) -> None:
        self._database = database
        self._provider = provider
        self._signer = signer
        self._public_base_url = public_base_url.rstrip("/")

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

    @staticmethod
    def _invalid(message: str) -> AgentBoxError:
        return AgentBoxError(
            ErrorCode.INVALID_REQUEST,
            message,
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=422,
        )
