"""PostgreSQL repositories for Agent Host v2.

Transport delivery is intentionally at-least-once. These repositories enforce
the durable fencing, checkpoint, and event-deduplication rules that make replay
safe across API and host restarts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCheckpoint,
    AgentHostCommand,
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostEvent,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostIntegrationHealth,
    AgentHostIntegrationSnapshot,
    AgentHostRunCheckpoint,
    AgentHostRunSpec,
    AgentHostRunState,
    AgentHostStatus,
    HostHello,
    canonical_json_sha256,
    checkpoint_advances,
    validate_agent_host_selections,
)
from app.modules.agent.infrastructure.models import (
    AgentHostAuthNonceModel,
    AgentHostCommandModel,
    AgentHostEventModel,
    AgentHostIntegrationModel,
    AgentHostMcpRouteModel,
    AgentHostModel,
    AgentHostPairingModel,
    AgentHostRunLeaseModel,
)


DEFAULT_PAIRING_TTL_SECONDS = 600
DEFAULT_COMMAND_TTL_SECONDS = 300
DEFAULT_RUN_LEASE_SECONDS = 90


class AgentHostRepositoryError(RuntimeError):
    """Base typed failure for the Agent Host persistence contract."""


class AgentHostNotFound(AgentHostRepositoryError):
    pass


class AgentHostPairingRejected(AgentHostRepositoryError):
    pass


class AgentHostProtocolViolation(AgentHostRepositoryError):
    pass


class AgentHostRunConflict(AgentHostRepositoryError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
            host.organization_id = pairing.organization_id
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

    async def get(self, host_id: UUID, *, for_update: bool = False) -> AgentHostModel | None:
        stmt = select(AgentHostModel).where(AgentHostModel.id == host_id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def require(self, host_id: UUID, *, for_update: bool = False) -> AgentHostModel:
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
            explicitly_draining = (
                capacity.get("available_runs") == 0
                and capacity.get("active_runs", 0) < capacity.get("max_runs", 0)
            )
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
        host = await self.get_for_user(host_id=host_id, user_id=user_id)
        if host is None:
            raise AgentHostNotFound("Agent Host was not found")
        host.revoked_at = now or utcnow()
        host.status = AgentHostStatus.REVOKED.value
        await self.session.flush()
        return host

    async def publish_integration(
        self,
        *,
        host_id: UUID,
        snapshot: AgentHostIntegrationSnapshot,
    ) -> AgentHostIntegrationModel:
        host = await self.require(host_id)
        if host.revoked_at is not None:
            raise AgentHostProtocolViolation("Agent Host is revoked")
        integration = (
            await self.session.execute(
                select(AgentHostIntegrationModel)
                .where(
                    AgentHostIntegrationModel.host_id == host_id,
                    AgentHostIntegrationModel.integration_key
                    == snapshot.integration_key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        values = {
            "display_name": snapshot.display_name,
            "adapter_protocol": snapshot.adapter_protocol.value,
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
            "integration_metadata": snapshot.metadata,
        }
        if integration is None:
            integration = AgentHostIntegrationModel(
                host_id=host_id,
                integration_key=snapshot.integration_key,
                **values,
            )
            self.session.add(integration)
        else:
            for key, value in values.items():
                setattr(integration, key, value)
        await self.session.flush()
        return integration

    async def get_integration(
        self,
        *,
        integration_id: UUID,
        for_update: bool = False,
    ) -> AgentHostIntegrationModel | None:
        stmt = select(AgentHostIntegrationModel).where(
            AgentHostIntegrationModel.id == integration_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_integrations(
        self,
        *,
        host_id: UUID,
    ) -> list[AgentHostIntegrationModel]:
        result = await self.session.execute(
            select(AgentHostIntegrationModel)
            .where(AgentHostIntegrationModel.host_id == host_id)
            .order_by(AgentHostIntegrationModel.display_name.asc())
        )
        return list(result.scalars())


class AgentHostDispatchRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def enqueue_run(
        self,
        *,
        host_id: UUID,
        integration_id: UUID,
        runtime_profile_id: UUID,
        run_spec: AgentHostRunSpec,
        encrypted_mcp_payload: dict,
        now: datetime | None = None,
        command_ttl_seconds: int = DEFAULT_COMMAND_TTL_SECONDS,
    ) -> AgentHostCommandModel:
        timestamp = now or utcnow()
        existing_lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_spec.agent_run_id,
            with_for_update=True,
        )
        if existing_lease is not None:
            existing = (
                await self.session.execute(
                    select(AgentHostCommandModel)
                    .where(
                        AgentHostCommandModel.run_id == run_spec.agent_run_id,
                        AgentHostCommandModel.kind
                        == AgentHostCommandKind.START_RUN.value,
                        AgentHostCommandModel.lease_epoch
                        == existing_lease.lease_epoch,
                    )
                    .order_by(AgentHostCommandModel.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if (
                existing is None
                or existing.host_id != host_id
                or existing_lease.integration_id != integration_id
                or existing_lease.runtime_profile_id != runtime_profile_id
            ):
                raise AgentHostRunConflict(
                    "agent run already has a different Agent Host dispatch"
                )
            return existing

        integration = await AgentHostRepository(self.uow).get_integration(
            integration_id=integration_id
        )
        if integration is None or integration.host_id != host_id:
            raise AgentHostNotFound("Agent Host integration was not found")
        if integration.health != AgentHostIntegrationHealth.READY.value:
            raise AgentHostRunConflict(
                f"integration is not ready: {integration.health}"
            )
        if integration.config_revision != run_spec.profile_revision:
            raise AgentHostRunConflict(
                "integration configuration changed after profile validation"
            )
        try:
            validate_agent_host_selections(
                config_options=integration.config_options or [],
                selections=run_spec.config_selections,
            )
        except ValueError as exc:
            raise AgentHostRunConflict(str(exc)) from exc

        lease = AgentHostRunLeaseModel(
            run_id=run_spec.agent_run_id,
            host_id=host_id,
            integration_id=integration_id,
            runtime_profile_id=runtime_profile_id,
            lease_epoch=1,
            state=AgentHostRunState.QUEUED_FOR_HOST.value,
            checkpoint=None,
            lease_expires_at=timestamp + timedelta(seconds=command_ttl_seconds),
            acked_event_sequence=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        payload = run_spec.model_dump(mode="json")
        command = AgentHostCommandModel(
            host_id=host_id,
            run_id=run_spec.agent_run_id,
            kind=AgentHostCommandKind.START_RUN.value,
            lease_epoch=lease.lease_epoch,
            payload_digest=canonical_json_sha256(payload),
            payload=payload,
            state=AgentHostCommandState.QUEUED.value,
            expires_at=timestamp + timedelta(seconds=command_ttl_seconds),
        )
        self.session.add_all([lease, command])
        try:
            route_id = UUID(run_spec.mcp_route_id)
        except ValueError as exc:
            raise AgentHostProtocolViolation("MCP route ID must be a UUID") from exc
        self.session.add(
            AgentHostMcpRouteModel(
                id=route_id,
                host_id=host_id,
                run_id=run_spec.agent_run_id,
                lease_epoch=lease.lease_epoch,
                encrypted_payload=encrypted_mcp_payload,
                expires_at=run_spec.run_deadline,
            )
        )
        await self.session.flush()
        return command

    async def resolve_mcp_route(
        self,
        *,
        route_id: UUID,
        host_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostMcpRouteModel:
        timestamp = now or utcnow()
        route = await self.session.get(
            AgentHostMcpRouteModel,
            route_id,
            with_for_update=True,
        )
        if route is None or route.host_id != host_id:
            raise AgentHostNotFound("MCP route was not found")
        if route.revoked_at is not None or route.expires_at < timestamp:
            raise AgentHostProtocolViolation("MCP route is expired or revoked")
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            route.run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or lease.host_id != host_id
            or lease.lease_epoch != route.lease_epoch
            or lease.lease_expires_at < timestamp
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        ):
            raise AgentHostProtocolViolation("MCP route lease is no longer active")
        route.last_resolved_at = timestamp
        await self.session.flush()
        return route

    async def poll_commands(
        self,
        *,
        host_id: UUID,
        limit: int,
        acknowledged_command_ids: list[UUID],
        checkpoints: list[AgentHostRunCheckpoint],
        available_run_slots: int,
        now: datetime | None = None,
        lease_seconds: int = DEFAULT_RUN_LEASE_SECONDS,
    ) -> list[AgentHostCommand]:
        timestamp = now or utcnow()
        await self._acknowledge_commands(
            host_id=host_id,
            command_ids=acknowledged_command_ids,
            now=timestamp,
        )
        for checkpoint in checkpoints:
            await self.apply_checkpoint(
                host_id=host_id,
                checkpoint=checkpoint,
                now=timestamp,
                lease_seconds=lease_seconds,
            )

        result = await self.session.execute(
            select(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.host_id == host_id,
                AgentHostCommandModel.state.in_(
                    [
                        AgentHostCommandState.QUEUED.value,
                        AgentHostCommandState.DELIVERED.value,
                    ]
                ),
                AgentHostCommandModel.expires_at >= timestamp,
            )
            .order_by(AgentHostCommandModel.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        commands = list(result.scalars())
        wire_commands: list[AgentHostCommand] = []
        remaining_run_slots = max(0, available_run_slots)
        for command in commands:
            if (
                command.kind == AgentHostCommandKind.START_RUN.value
                and remaining_run_slots == 0
            ):
                continue
            command.state = AgentHostCommandState.DELIVERED.value
            command.delivered_at = timestamp
            if (
                command.run_id is not None
                and command.lease_epoch is not None
                and command.kind == AgentHostCommandKind.START_RUN.value
            ):
                lease = await self.session.get(
                    AgentHostRunLeaseModel,
                    command.run_id,
                    with_for_update=True,
                )
                if lease is None or lease.lease_epoch != command.lease_epoch:
                    command.state = AgentHostCommandState.CANCELLED.value
                    continue
                if lease.state == AgentHostRunState.QUEUED_FOR_HOST.value:
                    lease.state = AgentHostRunState.LEASED.value
                lease.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
                lease.updated_at = timestamp
                remaining_run_slots -= 1
            wire_commands.append(self._wire_command(command))
        await self.session.flush()
        return wire_commands

    async def _acknowledge_commands(
        self,
        *,
        host_id: UUID,
        command_ids: list[UUID],
        now: datetime,
    ) -> None:
        if not command_ids:
            return
        result = await self.session.execute(
            select(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.host_id == host_id,
                AgentHostCommandModel.id.in_(command_ids),
            )
            .with_for_update()
        )
        found = {command.id: command for command in result.scalars()}
        for command_id in command_ids:
            command = found.get(command_id)
            if command is None:
                raise AgentHostProtocolViolation(
                    f"command {command_id} does not belong to this host"
                )
            if command.state in {
                AgentHostCommandState.CANCELLED.value,
                AgentHostCommandState.EXPIRED.value,
            }:
                continue
            command.state = AgentHostCommandState.ACKNOWLEDGED.value
            command.acknowledged_at = now

    async def apply_checkpoint(
        self,
        *,
        host_id: UUID,
        checkpoint: AgentHostRunCheckpoint,
        now: datetime | None = None,
        lease_seconds: int = DEFAULT_RUN_LEASE_SECONDS,
    ) -> AgentHostRunLeaseModel:
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            checkpoint.run_id,
            with_for_update=True,
        )
        if lease is None or lease.host_id != host_id:
            raise AgentHostNotFound("run lease does not belong to this host")
        if lease.lease_epoch != checkpoint.lease_epoch:
            raise AgentHostProtocolViolation("stale run lease epoch")
        previous = (
            AgentHostCheckpoint(lease.checkpoint) if lease.checkpoint is not None else None
        )
        if not checkpoint_advances(previous, checkpoint.checkpoint):
            raise AgentHostProtocolViolation("run checkpoint regressed")
        current_state = AgentHostRunState(lease.state)
        if current_state in TERMINAL_AGENT_HOST_RUN_STATES:
            if checkpoint.state is not current_state:
                raise AgentHostProtocolViolation("terminal run state cannot change")
            return lease
        if checkpoint.state in TERMINAL_AGENT_HOST_RUN_STATES:
            lease.terminal_at = timestamp
        lease.checkpoint = checkpoint.checkpoint.value
        lease.state = checkpoint.state.value
        lease.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
        lease.updated_at = timestamp
        await self.session.flush()
        return lease

    async def append_events(
        self,
        *,
        host_id: UUID,
        batch: AgentHostEventBatch,
        now: datetime | None = None,
    ) -> AgentHostEventAck:
        timestamp = now or utcnow()
        first = batch.events[0]
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            first.run_id,
            with_for_update=True,
        )
        if lease is None or lease.host_id != host_id:
            raise AgentHostNotFound("run lease does not belong to this host")
        if lease.lease_epoch != first.lease_epoch:
            raise AgentHostProtocolViolation("stale run lease epoch")

        expected = lease.acked_event_sequence + 1
        for event in batch.events:
            if event.sequence < expected:
                existing = (
                    await self.session.execute(
                        select(AgentHostEventModel)
                        .where(
                            AgentHostEventModel.run_id == event.run_id,
                            AgentHostEventModel.lease_epoch == event.lease_epoch,
                            AgentHostEventModel.sequence == event.sequence,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing is None or existing.payload_digest != event.payload_digest:
                    raise AgentHostProtocolViolation(
                        "replayed event sequence has a different digest"
                    )
                continue
            if event.sequence != expected:
                raise AgentHostProtocolViolation(
                    f"event sequence gap: expected {expected}, got {event.sequence}"
                )
            self.session.add(self._event_model(event))
            expected += 1

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise AgentHostProtocolViolation(
                "event ID or sequence conflicts with an existing event"
            ) from exc
        lease.acked_event_sequence = max(
            lease.acked_event_sequence,
            expected - 1,
        )
        lease.lease_expires_at = max(lease.lease_expires_at, timestamp)
        lease.updated_at = timestamp
        await self.session.flush()
        return AgentHostEventAck(
            run_id=lease.run_id,
            lease_epoch=lease.lease_epoch,
            acked_through=lease.acked_event_sequence,
        )

    async def events_after(
        self,
        *,
        run_id: UUID,
        sequence: int,
        limit: int = 256,
    ) -> list[AgentHostEventModel]:
        result = await self.session.execute(
            select(AgentHostEventModel)
            .where(
                AgentHostEventModel.run_id == run_id,
                AgentHostEventModel.sequence > sequence,
            )
            .order_by(AgentHostEventModel.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars())

    async def get_run_lease(
        self,
        *,
        run_id: UUID,
    ) -> AgentHostRunLeaseModel | None:
        return await self.session.get(AgentHostRunLeaseModel, run_id)

    async def expire_unaccepted_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if lease is None or lease.checkpoint is not None:
            return False
        if AgentHostRunState(lease.state) not in {
            AgentHostRunState.QUEUED_FOR_HOST,
            AgentHostRunState.LEASED,
        }:
            return False
        lease.state = AgentHostRunState.FAILED.value
        lease.error_code = "HOST_WAIT_TIMEOUT"
        lease.error_detail = "No Agent Host accepted the run before its wait deadline"
        lease.terminal_at = timestamp
        lease.updated_at = timestamp
        await self.session.flush()
        return True

    async def reconcile_expired_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
        recovery_grace_seconds: int = 120,
    ) -> AgentHostRunLeaseModel | None:
        """Advance an expired, accepted lease without risking duplicate work.

        A provider prompt may already have crossed the process boundary after
        ACCEPTED. We therefore never retry an accepted turn automatically.
        The first observed expiry enters a bounded recovery window; a second
        expiry terminates as DISPATCH_UNKNOWN. A reconnecting host can move
        RECOVERING back to its durable RUNNING checkpoint.
        """

        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or lease.lease_expires_at >= timestamp
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
            or lease.checkpoint is None
        ):
            return lease

        if AgentHostRunState(lease.state) is AgentHostRunState.RECOVERING:
            lease.state = AgentHostRunState.DISPATCH_UNKNOWN.value
            lease.checkpoint = AgentHostCheckpoint.TERMINAL.value
            lease.error_code = "HOST_LEASE_EXPIRED"
            lease.error_detail = (
                "The Agent Host disconnected after accepting the run; "
                "Lemma did not repeat the turn because provider dispatch "
                "could not be ruled out"
            )
            lease.terminal_at = timestamp
            lease.lease_expires_at = timestamp
        else:
            lease.state = AgentHostRunState.RECOVERING.value
            lease.checkpoint = AgentHostCheckpoint.RECOVERING.value
            lease.error_code = "HOST_RECOVERING"
            lease.error_detail = "Waiting for the Agent Host to reconnect"
            lease.lease_expires_at = timestamp + timedelta(
                seconds=recovery_grace_seconds
            )
        lease.updated_at = timestamp
        await self.session.flush()
        return lease

    async def enqueue_cancel(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostCommandModel | None:
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if lease is None or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES:
            return None
        payload = {"agent_run_id": str(run_id)}
        command = AgentHostCommandModel(
            host_id=lease.host_id,
            run_id=run_id,
            kind=AgentHostCommandKind.CANCEL_RUN.value,
            lease_epoch=lease.lease_epoch,
            payload_digest=canonical_json_sha256(payload),
            payload=payload,
            state=AgentHostCommandState.QUEUED.value,
            expires_at=timestamp + timedelta(seconds=DEFAULT_COMMAND_TTL_SECONDS),
        )
        self.session.add(command)
        await self.session.flush()
        return command

    @staticmethod
    def _wire_command(command: AgentHostCommandModel) -> AgentHostCommand:
        return AgentHostCommand(
            command_id=command.id,
            kind=AgentHostCommandKind(command.kind),
            created_at=command.created_at,
            expires_at=command.expires_at,
            run_id=command.run_id,
            lease_epoch=command.lease_epoch,
            payload_sha256=command.payload_digest,
            payload=command.payload or {},
        )

    @staticmethod
    def _event_model(event: AgentHostEvent) -> AgentHostEventModel:
        return AgentHostEventModel(
            run_id=event.run_id,
            lease_epoch=event.lease_epoch,
            sequence=event.sequence,
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            type=event.type.value,
            object_id=event.object_id,
            payload=event.payload,
            payload_digest=event.payload_digest,
            integration_key=event.integration_key,
            adapter_version=event.adapter_version,
        )
