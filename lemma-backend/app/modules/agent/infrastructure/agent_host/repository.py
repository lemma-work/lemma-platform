"""Pairing, host identity, and harness persistence for Agent Host.

Run dispatch lives in a separate repository so this module stays scoped to
identity: who is paired, whether they are alive, and what they can run.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessSnapshot,
    AgentHostStatus,
    HostHello,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
    DEFAULT_PAIRING_TTL_SECONDS,
    AgentHostNotFound,
    AgentHostPairingRejected,
    AgentHostProtocolViolation,
    utcnow,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostHarnessModel,
    AgentHostModel,
    AgentHostPairingModel,
)


# Heartbeat rows are rewritten at most this often; the 90s offline threshold
# leaves ample slack for a host whose link heartbeats every 20s.
_SEEN_WRITE_INTERVAL_SECONDS = 20


def _negotiated_status(
    hello: HostHello, capacity: dict[str, int]
) -> tuple[int | None, AgentHostStatus]:
    """The protocol a heartbeat negotiates, and the status it puts the host in."""
    try:
        protocol = hello.negotiate()
    except ValueError:
        return None, AgentHostStatus.UPGRADE_REQUIRED
    explicitly_draining = capacity.get("available_runs") == 0 and capacity.get(
        "active_runs", 0
    ) < capacity.get("max_runs", 0)
    return protocol, (
        AgentHostStatus.DRAINING if explicitly_draining else AgentHostStatus.ONLINE
    )


def _row_already_says(
    host: AgentHostModel,
    *,
    protocol: int | None,
    host_release: str,
    status: AgentHostStatus,
    capacity: dict[str, int],
    capabilities: dict[str, object] | None,
) -> bool:
    """Whether a heartbeat would write nothing new. ``None`` capabilities: kept."""
    return (
        host.protocol_version == protocol
        and host.host_release == host_release
        and host.status == status.value
        and (host.capacity or {}) == capacity
        and (capabilities is None or (host.capabilities or {}) == capabilities)
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
        host_secret_hash: str,
        display_name: str,
        hello: HostHello,
        now: datetime | None = None,
    ) -> AgentHostModel:
        """Create or re-pair a host; re-pairing rotates the host secret."""
        timestamp = now or utcnow()
        pairing = (
            await self.session.execute(
                select(AgentHostPairingModel)
                .where(AgentHostPairingModel.code_hash == code_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if pairing is None or pairing.expires_at < timestamp:
            raise AgentHostPairingRejected("pairing code is invalid or expired")

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
                    AgentHostModel.installation_id == hello.installation_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if host is None:
            host = AgentHostModel(
                user_id=pairing.user_id,
                installation_id=hello.installation_id,
                host_secret_hash=host_secret_hash,
                display_name=display_name.strip() or pairing.display_name,
                status=status.value,
                protocol_version=selected_protocol,
                host_release=hello.host_release,
                capacity={},
                last_seen_at=None,
                revoked_at=None,
            )
            self.session.add(host)
        else:
            host.host_secret_hash = host_secret_hash
            host.display_name = display_name.strip() or pairing.display_name
            host.status = status.value
            host.protocol_version = selected_protocol
            host.host_release = hello.host_release
            host.revoked_at = None

        await self.session.delete(pairing)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise AgentHostPairingRejected(
                "host installation is already paired"
            ) from exc
        return host

    async def get(
        self, host_id: UUID, *, for_update: bool = False
    ) -> AgentHostModel | None:
        stmt = select(AgentHostModel).where(AgentHostModel.id == host_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_many(self, host_ids: set[UUID]) -> dict[UUID, AgentHostModel]:
        if not host_ids:
            return {}
        result = await self.session.execute(
            select(AgentHostModel).where(AgentHostModel.id.in_(host_ids))
        )
        return {host.id: host for host in result.scalars()}

    async def get_by_secret_hash(self, secret_hash: str) -> AgentHostModel | None:
        return (
            await self.session.execute(
                select(AgentHostModel).where(
                    AgentHostModel.host_secret_hash == secret_hash
                )
            )
        ).scalar_one_or_none()

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

    async def mark_seen(
        self,
        *,
        host_id: UUID,
        hello: HostHello,
        capacity: dict,
        capabilities: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> AgentHostModel:
        """Record one heartbeat, rewriting the row only when something changed.

        ``control`` frames arrive at least every 20s; skipping no-op writes
        keeps an idle host from producing a locked row update on every one.
        """
        timestamp = now or utcnow()
        host = await self.require(host_id)
        if host.revoked_at is not None:
            raise AgentHostProtocolViolation("Agent Host is revoked")
        if host.installation_id != hello.installation_id:
            raise AgentHostProtocolViolation("installation identity changed")

        protocol, status = _negotiated_status(hello, capacity)
        recently_seen = host.last_seen_at is not None and host.last_seen_at > (
            timestamp - timedelta(seconds=_SEEN_WRITE_INTERVAL_SECONDS)
        )
        if recently_seen and _row_already_says(
            host,
            protocol=protocol,
            host_release=hello.host_release,
            status=status,
            capacity=capacity,
            capabilities=capabilities,
        ):
            return host

        host = await self.require(host_id, for_update=True)
        if host.revoked_at is not None:
            raise AgentHostProtocolViolation("Agent Host is revoked")
        host.protocol_version = protocol
        host.host_release = hello.host_release
        host.capacity = capacity
        if capabilities is not None:
            host.capabilities = capabilities
        host.status = status.value
        host.last_seen_at = timestamp
        await self.session.flush()
        return host

    async def claim_link_generation(self, host_id: UUID) -> int:
        """Take the next link generation for a host: the one its new link owns.

        One ``UPDATE ... RETURNING``, so two handshakes racing on different
        replicas serialize on the row lock and come away with distinct,
        ordered values. Whichever is greater owns the host.
        """
        claimed = await self.session.execute(
            update(AgentHostModel)
            .where(AgentHostModel.id == host_id)
            .values(link_generation=AgentHostModel.link_generation + 1)
            .returning(AgentHostModel.link_generation)
            .execution_options(synchronize_session=False)
        )
        return int(claimed.scalar_one())

    async def link_generation(self, host_id: UUID) -> int | None:
        """The generation of the link that owns the host now; None if it is gone."""
        return (
            await self.session.execute(
                select(AgentHostModel.link_generation).where(
                    AgentHostModel.id == host_id
                )
            )
        ).scalar_one_or_none()

    async def mark_upgrade_required(self, secret_hash: str) -> UUID | None:
        """Record that the host holding this secret speaks a retired protocol.

        Returns the host's id only when this call changed it, so the caller
        can say so once per host rather than once per retry. Unknown and
        revoked secrets change nothing and return None.
        """
        return (
            await self.session.execute(
                update(AgentHostModel)
                .where(
                    AgentHostModel.host_secret_hash == secret_hash,
                    AgentHostModel.revoked_at.is_(None),
                    AgentHostModel.status != AgentHostStatus.UPGRADE_REQUIRED.value,
                )
                .values(status=AgentHostStatus.UPGRADE_REQUIRED.value)
                .returning(AgentHostModel.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()

    async def revoke(
        self,
        *,
        host_id: UUID,
        user_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostModel:
        """Revoke a host, invalidating its secret immediately.

        Cancelling the host's in-flight commands and run leases is added
        alongside the dispatch tables; there is no dispatch state to reconcile
        while this revision is the head.
        """
        host = await self.get(host_id, for_update=True)
        if host is None or host.user_id != user_id:
            raise AgentHostNotFound("Agent Host was not found")
        timestamp = now or utcnow()
        host.revoked_at = timestamp
        host.status = AgentHostStatus.REVOKED.value
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
                    AgentHostHarnessModel.harness_key == snapshot.harness_key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        values = {
            "display_name": snapshot.display_name,
            "adapter_version": snapshot.adapter_version,
            "upstream_version": snapshot.upstream_version,
            "health": snapshot.health.value,
            "capabilities": snapshot.capabilities.model_dump(mode="json"),
            "config_revision": snapshot.config_revision,
            "config_options": [
                option.model_dump(mode="json") for option in snapshot.config_options
            ],
            "stale_after": snapshot.stale_after,
            "stale_reason": snapshot.stale_reason,
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

    async def get_harnesses(
        self,
        harness_ids: set[UUID],
    ) -> dict[UUID, AgentHostHarnessModel]:
        if not harness_ids:
            return {}
        result = await self.session.execute(
            select(AgentHostHarnessModel).where(
                AgentHostHarnessModel.id.in_(harness_ids)
            )
        )
        return {harness.id: harness for harness in result.scalars()}

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
