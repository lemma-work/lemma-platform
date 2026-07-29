"""PostgreSQL repositories for Agent Host v2.

Transport delivery is intentionally at-least-once. These repositories enforce
the durable fencing, checkpoint, and event-deduplication rules that make replay
safe across API and host restarts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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
    AgentHostRunCheckpoint,
    AgentHostRunSpec,
    AgentHostRunState,
    canonical_json_sha256,
    checkpoint_advances,
    validate_agent_host_selections,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.agent_host_recovery_repository import (
    AgentHostRecoveryRepositoryMixin,
)
from app.modules.agent.infrastructure.agent_host_repository_common import (
    DEFAULT_COMMAND_TTL_SECONDS,
    DEFAULT_RUN_LEASE_SECONDS,
    AgentHostNotFound,
    AgentHostProtocolViolation,
    AgentHostRunConflict,
    utcnow,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostEventModel,
    AgentHostMcpRouteModel,
    AgentHostRunLeaseModel,
)


class AgentHostDispatchRepository(AgentHostRecoveryRepositoryMixin):
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
                        AgentHostCommandModel.lease_epoch == existing_lease.lease_epoch,
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

        host_repository = AgentHostRepository(self.uow)
        host = await host_repository.require(host_id, for_update=True)
        if host.revoked_at is not None:
            raise AgentHostRunConflict("Agent Host is revoked")
        integration = await host_repository.get_integration(
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
                if AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES:
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
            AgentHostCheckpoint(lease.checkpoint)
            if lease.checkpoint is not None
            else None
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
        if any(
            event.run_id != first.run_id or event.lease_epoch != first.lease_epoch
            for event in batch.events
        ):
            raise AgentHostProtocolViolation("event batch spans multiple run leases")
        expected = lease.acked_event_sequence + 1
        terminal = AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
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
            if terminal:
                raise AgentHostProtocolViolation("terminal run cannot accept events")
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
        if (
            lease is None
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        ):
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
