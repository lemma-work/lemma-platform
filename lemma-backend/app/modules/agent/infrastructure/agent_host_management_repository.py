"""Pairing, host identity, and harness persistence for Agent Host."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCheckpoint,
    AgentHostCommandState,
    AgentHostHarnessSnapshot,
    AgentHostRunState,
    AgentHostStatus,
    HostHello,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    DEFAULT_PAIRING_TTL_SECONDS,
    AgentHostNotFound,
    AgentHostPairingRejected,
    AgentHostProtocolViolation,
    utcnow,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostAuthNonceModel,
    AgentHostCommandModel,
    AgentHostHarnessModel,
    AgentHostMcpRouteModel,
    AgentHostModel,
    AgentHostPairingModel,
    AgentHostRunLeaseModel,
)


class AgentHostRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create_pairing(
        self,
        *,
        pairing_id: UUID,
        user_id: UUID,
        organization_id: UUID | None,
        code_hash: str,
        display_name: str,
        now: datetime | None = None,
        ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
    ) -> AgentHostPairingModel:
        timestamp = now or utcnow()
        await self.session.execute(
            delete(AgentHostPairingModel).where(
                AgentHostPairingModel.expires_at < timestamp
            )
        )
        pairing = AgentHostPairingModel(
            id=pairing_id,
            user_id=user_id,
            organization_id=organization_id,
            code_hash=code_hash,
            display_name=display_name.strip(),
            expires_at=timestamp + timedelta(seconds=ttl_seconds),
        )
        self.session.add(pairing)
        await self.session.flush()
        return pairing

    async def consume_pairing(
        self,
        *,
        code_hash: str,
        public_key: str,
        public_key_fingerprint: str,
        display_name: str,
        hello: HostHello,
        now: datetime | None = None,
    ) -> AgentHostModel:
        timestamp = now or utcnow()
        pairing = (
            await self.session.execute(
                select(AgentHostPairingModel)
                .where(AgentHostPairingModel.code_hash == code_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            pairing is None
            or pairing.consumed_at is not None
            or pairing.expires_at < timestamp
        ):
            raise AgentHostPairingRejected("pairing code is invalid, used, or expired")

        selected_protocol: int | None
        status: AgentHostStatus
        try:
            selected_protocol = hello.negotiate()
            status = AgentHostStatus.OFFLINE
        except ValueError:
            selected_protocol = None
            status = AgentHostStatus.UPGRADE_REQUIRED

        host = (
            await self.session.execute(
                select(AgentHostModel)
                .where(
                    AgentHostModel.user_id == pairing.user_id,
                    AgentHostModel.organization_id == pairing.organization_id,
                    AgentHostModel.installation_id == hello.installation_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if host is None:
            host = AgentHostModel(
                user_id=pairing.user_id,
                organization_id=pairing.organization_id,
                installation_id=hello.installation_id,
                public_key=public_key,
                public_key_fingerprint=public_key_fingerprint,
                display_name=display_name.strip() or pairing.display_name,
                status=status.value,
                protocol_min=hello.protocol_min,
                protocol_max=hello.protocol_max,
                protocol_version=selected_protocol,
                host_release=hello.host_release,
                adapter_manifest_id=hello.adapter_manifest_id,
                instance_id=hello.instance_id,
                capacity={},
                last_seen_at=None,
                revoked_at=None,
            )
            self.session.add(host)
        else:
            host.public_key = public_key
            host.public_key_fingerprint = public_key_fingerprint
            host.display_name = display_name.strip() or pairing.display_name
            host.status = status.value
            host.protocol_min = hello.protocol_min
            host.protocol_max = hello.protocol_max
            host.protocol_version = selected_protocol
            host.host_release = hello.host_release
            host.adapter_manifest_id = hello.adapter_manifest_id
            host.instance_id = hello.instance_id
            host.revoked_at = None

        pairing.consumed_at = timestamp
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise AgentHostPairingRejected(
                "public key is already paired to another Agent Host"
            ) from exc
        return host

    async def get(
        self, host_id: UUID, *, for_update: bool = False
    ) -> AgentHostModel | None:
        stmt = select(AgentHostModel).where(AgentHostModel.id == host_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def require(
        self, host_id: UUID, *, for_update: bool = False
    ) -> AgentHostModel:
        host = await self.get(host_id, for_update=for_update)
        if host is None:
            raise AgentHostNotFound("Agent Host was not found")
        return host

    async def get_for_user(
        self,
        *,
        host_id: UUID,
        user_id: UUID,
    ) -> AgentHostModel | None:
        return (
            await self.session.execute(
                select(AgentHostModel).where(
                    AgentHostModel.id == host_id,
                    AgentHostModel.user_id == user_id,
                )
            )
        ).scalar_one_or_none()

    async def list_for_user(self, *, user_id: UUID) -> list[AgentHostModel]:
        result = await self.session.execute(
            select(AgentHostModel)
            .where(AgentHostModel.user_id == user_id)
            .order_by(
                AgentHostModel.revoked_at.asc().nullsfirst(),
                AgentHostModel.last_seen_at.desc().nullslast(),
                AgentHostModel.created_at.desc(),
            )
        )
        return list(result.scalars())

    async def record_nonce(
        self,
        *,
        host_id: UUID,
        nonce_hash: str,
        expires_at: datetime,
    ) -> None:
        await self.session.execute(
            delete(AgentHostAuthNonceModel).where(
                AgentHostAuthNonceModel.expires_at < utcnow()
            )
        )
        self.session.add(
            AgentHostAuthNonceModel(
                host_id=host_id,
                nonce_hash=nonce_hash,
                expires_at=expires_at,
            )
        )
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise AgentHostProtocolViolation("host nonce was already used") from exc

    async def mark_seen(
        self,
        *,
        host_id: UUID,
        hello: HostHello,
        capacity: dict,
        now: datetime | None = None,
    ) -> AgentHostModel:
        timestamp = now or utcnow()
        host = await self.require(host_id, for_update=True)
        if host.revoked_at is not None:
            host.status = AgentHostStatus.REVOKED.value
            raise AgentHostProtocolViolation("Agent Host is revoked")
        if host.installation_id != hello.installation_id:
            raise AgentHostProtocolViolation("installation identity changed")
        host.protocol_min = hello.protocol_min
        host.protocol_max = hello.protocol_max
        host.host_release = hello.host_release
        host.adapter_manifest_id = hello.adapter_manifest_id
        host.instance_id = hello.instance_id
        host.capacity = capacity
        host.last_seen_at = timestamp
        try:
            host.protocol_version = hello.negotiate()
            explicitly_draining = capacity.get("available_runs") == 0 and capacity.get(
                "active_runs", 0
            ) < capacity.get("max_runs", 0)
            if explicitly_draining:
                host.status = AgentHostStatus.DRAINING.value
            elif host.status != AgentHostStatus.DEGRADED.value:
                host.status = AgentHostStatus.ONLINE.value
        except ValueError:
            host.protocol_version = None
            host.status = AgentHostStatus.UPGRADE_REQUIRED.value
        await self.session.flush()
        return host

    async def revoke(
        self,
        *,
        host_id: UUID,
        user_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostModel:
        host = await self.get(host_id, for_update=True)
        if host is None or host.user_id != user_id:
            raise AgentHostNotFound("Agent Host was not found")
        timestamp = now or utcnow()
        host.revoked_at = timestamp
        host.status = AgentHostStatus.REVOKED.value
        command_rows = await self.session.execute(
            select(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.host_id == host_id,
                AgentHostCommandModel.state.in_(
                    [
                        AgentHostCommandState.QUEUED.value,
                        AgentHostCommandState.DELIVERED.value,
                    ]
                ),
            )
            .with_for_update()
        )
        for command in command_rows.scalars():
            command.state = AgentHostCommandState.CANCELLED.value
        lease_rows = await self.session.execute(
            select(AgentHostRunLeaseModel)
            .where(AgentHostRunLeaseModel.host_id == host_id)
            .with_for_update()
        )
        for lease in lease_rows.scalars():
            if AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES:
                continue
            lease.state = AgentHostRunState.CANCELLED.value
            lease.checkpoint = AgentHostCheckpoint.TERMINAL.value
            lease.error_code = "HOST_REVOKED"
            lease.error_detail = "The Agent Host was revoked by its owner"
            lease.terminal_at = timestamp
            lease.lease_expires_at = timestamp
            lease.updated_at = timestamp
        route_rows = await self.session.execute(
            select(AgentHostMcpRouteModel)
            .where(
                AgentHostMcpRouteModel.host_id == host_id,
                AgentHostMcpRouteModel.revoked_at.is_(None),
            )
            .with_for_update()
        )
        for route in route_rows.scalars():
            route.revoked_at = timestamp
        await self.session.flush()
        return host

    async def publish_harness(
        self,
        *,
        host_id: UUID,
        snapshot: AgentHostHarnessSnapshot,
    ) -> AgentHostHarnessModel:
        host = await self.require(host_id)
        if host.revoked_at is not None:
            raise AgentHostProtocolViolation("Agent Host is revoked")
        harness = (
            await self.session.execute(
                select(AgentHostHarnessModel)
                .where(
                    AgentHostHarnessModel.host_id == host_id,
                    AgentHostHarnessModel.harness_key
                    == snapshot.harness_key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        values = {
            "display_name": snapshot.display_name,
            "adapter_protocol": snapshot.adapter_protocol.value,
            "adapter_protocol_version": snapshot.adapter_protocol_version,
            "adapter_version": snapshot.adapter_version,
            "upstream_version": snapshot.upstream_version,
            "auth_state": snapshot.auth_state,
            "health": snapshot.health.value,
            "capabilities": snapshot.capabilities.model_dump(mode="json"),
            "config_revision": snapshot.config_revision,
            "config_options": [
                option.model_dump(mode="json") for option in snapshot.config_options
            ],
            "fetched_at": snapshot.fetched_at,
            "stale_after": snapshot.stale_after,
            "stale_reason": snapshot.stale_reason,
            "harness_metadata": snapshot.metadata,
        }
        if harness is None:
            harness = AgentHostHarnessModel(
                host_id=host_id,
                harness_key=snapshot.harness_key,
                **values,
            )
            self.session.add(harness)
        else:
            for key, value in values.items():
                setattr(harness, key, value)
        await self.session.flush()
        return harness

    async def get_harness(
        self,
        *,
        harness_id: UUID,
        for_update: bool = False,
    ) -> AgentHostHarnessModel | None:
        stmt = select(AgentHostHarnessModel).where(
            AgentHostHarnessModel.id == harness_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_harnesses(
        self,
        *,
        host_id: UUID,
    ) -> list[AgentHostHarnessModel]:
        result = await self.session.execute(
            select(AgentHostHarnessModel)
            .where(AgentHostHarnessModel.host_id == host_id)
            .order_by(AgentHostHarnessModel.display_name.asc())
        )
        return list(result.scalars())
